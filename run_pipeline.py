#!/usr/bin/env python3
"""
自动化流水线：Solver → JSON/Parquet → Hugging Face

每个 batch 求解完成后，检查结果目录下文件数量。达到 batch_size 阈值时
尝试上传一次（默认按 120 秒 / 5 文件线性缩放）。若上传失败或超时，文件留在结果目录，
本轮关闭中途上传，仅继续求解；全部求解完成后再统一尝试上传一次。

用法:
  python run_pipeline.py                         # 默认求解全部牌面
  python run_pipeline.py 1-20                    # 求解序号 1-20
  python run_pipeline.py 1,5,10,15,20            # 指定序号
  python run_pipeline.py Jc7c5c                  # 指定具体牌面（大小写不敏感，如 jc7c5c）
  python run_pipeline.py Jc7c5c,AcKc3d           # 多个牌面（逗号分隔，无空格）
  python run_pipeline.py "Jc,7c,5c"              # 也支持牌面内逗号格式（需引号）
  python run_pipeline.py 1-20 --no-upload        # 只求解+转换，不上传
  python run_pipeline.py 1-20 --convert-only     # 仅转换已有 JSON 并上传（不跑 solver）

环境变量:
  HF_TOKEN 或 HUGGINGFACE_HUB_TOKEN: 未登录时自动用此 token 登录
  HF_REPO_ID: 目标 Hugging Face dataset repo_id；不传则启动时交互输入
  PIPELINE_STATUS_FILE: 覆盖默认状态文件路径（供外部监控程序读取）

  dataset 名（repo_id 最后一段）默认对应 ranges 下的 range 文件名（不含路径）：
  例如 dataset 为 sia-12-sod-30 则匹配 ranges/sia-sod/sia-12-sod-30.txt，
  soa-50-sid-30 则匹配 ranges/soa-sid/soa-50-sid-30.txt，
  3ia-16.5-3od-13 则匹配 ranges/3ia-3od/3ia-16.5-3od-13.txt，
  sia-16-sod-21.5-open2.5 则匹配 ranges/sia-sod-open2.5/ 或文件名含 open2.5；
  根目录遗留的 sia-100bb.txt 仍可匹配。若同时传入 --scenario 与 --range-file，
  则优先使用显式指定的 ranges/<scenario>/<range-file>，repo_id 仅作为上传目标。

外部监控:
  运行时会写入 JSON 状态文件（默认 ~/run/solver_running_status.json），包含
  repo_id、scenario、当前 batch、pid 等。路径与 solver 工作目录无关，详见
  docs/PIPELINE_STATUS.md。SSH 监控程序可直接读取，例如:

    cat ~/run/solver_running_status.json
    jq -r '.repo_id' ~/run/solver_running_status.json
"""

import argparse
import atexit
import json
import os
import shlex
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
RESULTS_DIR = SCRIPT_DIR / "results"
UPLOAD_DIR = SCRIPT_DIR / "upload"
RANGES_DIR = SCRIPT_DIR / "ranges"
CARDS_DIR = SCRIPT_DIR / "cards"
SUPPORTED_EXPORT_FORMATS = ["json"] if sys.platform == "win32" else ["json", "parquet", "parquet_native"]
SUPPORTED_UPLOAD_FORMATS = ["json", "parquet"]
DEFAULT_EXPORT_FORMAT = "json" if sys.platform == "win32" else "parquet"
DEFAULT_UPLOAD_FORMAT = "parquet"
DEFAULT_UPLOAD_ATTEMPT_TIMEOUT_SECONDS = 120
UPLOAD_TIMEOUT_BASE_FILE_COUNT = 5
def _default_pipeline_status_file() -> Path:
    """固定默认路径（用户家目录下），与 solver 当前工作目录无关，且无需 root 权限。"""
    return Path.home() / "run" / "solver_running_status.json"


DEFAULT_PIPELINE_STATUS_FILE = _default_pipeline_status_file()

from auto_run_solver import (
    SCENARIO_CONFIG,
    SCENARIO_DEFAULTS,
    SCENARIO_SUBDIRS,
    infer_scenario_from_name,
    infer_scenario_from_range_path,
)
from solver_setting import (
    register_solver_setting_file,
    register_solver_setting_snapshot,
    snapshot_for_scenario,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_status_file(cli_path: Optional[str]) -> Path:
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    env_path = os.environ.get("PIPELINE_STATUS_FILE", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    return DEFAULT_PIPELINE_STATUS_FILE.resolve()


def _resolve_cli_range_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


def _resolve_result_dir(raw_path: Optional[str]) -> Path:
    if not raw_path:
        return RESULTS_DIR.resolve()
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_path.replace(path)


class PipelineStatusTracker:
    """将 pipeline 运行状态写入 JSON，供外部 SSH 监控程序读取。"""

    def __init__(self, path: Path):
        self.path = path
        self._data: Dict[str, Any] = {
            "started_at": _utc_now(),
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "status_file": str(path),
        }
        self._finalized = False
        atexit.register(self._atexit_finalize)

    def update(self, **fields: Any) -> None:
        if self._finalized:
            return
        self._data.update(fields)
        self._data["updated_at"] = _utc_now()
        self._data["pid"] = os.getpid()
        self._flush()

    def finalize(self, status: str, **fields: Any) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._data["status"] = status
        self._data.update(fields)
        self._data["finished_at"] = _utc_now()
        self._data["updated_at"] = self._data["finished_at"]
        self._data["pid"] = os.getpid()
        self._flush()

    def _atexit_finalize(self) -> None:
        if not self._finalized:
            self.finalize("exited")

    def _flush(self) -> None:
        try:
            _atomic_write_json(self.path, self._data)
        except OSError as e:
            print(f"[警告] 无法写入 pipeline 状态文件 {self.path}: {e}")


class PipelineRunSummary:
    """在 pipeline 退出时打印任务统计与牌面信息。"""

    def __init__(self) -> None:
        self.started_at = datetime.now()
        self._printed = False
        self.exit_reason = "正常结束"
        self.range_input = "all"
        self.command = " ".join(sys.argv)
        self.repo_id: Optional[str] = None
        self.scenario: Optional[str] = None
        self.range_file: Optional[str] = None
        self.all_boards: List[str] = []
        self.planned_indices: List[int] = []
        self.batches: List[List[int]] = []
        self.current_batch = 0
        self.total_batches = 0
        self.batch_size = 5
        self.export_format = DEFAULT_EXPORT_FORMAT
        self.upload_format = DEFAULT_UPLOAD_FORMAT
        self.result_dir = RESULTS_DIR
        self.upload_enabled = False
        self.upload_failures = 0
        self.upload_disabled_due_network = False
        self.convert_only = False

    def configure(
        self,
        *,
        range_input: str,
        repo_id: Optional[str],
        scenario: Optional[str],
        range_file: Optional[Path],
        all_boards: List[str],
        planned_indices: List[int],
        batches: List[List[int]],
        batch_size: int,
        export_format: str,
        upload_format: str,
        result_dir: Path,
        upload_enabled: bool,
        convert_only: bool = False,
    ) -> None:
        self.range_input = range_input
        self.repo_id = repo_id
        self.scenario = scenario
        if range_file is not None:
            try:
                self.range_file = str(range_file.resolve().relative_to(SCRIPT_DIR))
            except ValueError:
                self.range_file = range_file.name
        self.all_boards = all_boards
        self.planned_indices = planned_indices
        self.batches = batches
        self.total_batches = len(batches)
        self.batch_size = batch_size
        self.export_format = export_format
        self.upload_format = upload_format
        self.result_dir = result_dir
        self.upload_enabled = upload_enabled
        self.convert_only = convert_only
        self.command = " ".join(sys.argv)

    def set_batch(self, batch_num: int) -> None:
        self.current_batch = batch_num

    def set_upload_state(
        self,
        *,
        upload_enabled: Optional[bool] = None,
        upload_failures: Optional[int] = None,
        upload_disabled_due_network: Optional[bool] = None,
    ) -> None:
        if upload_enabled is not None:
            self.upload_enabled = upload_enabled
        if upload_failures is not None:
            self.upload_failures = upload_failures
        if upload_disabled_due_network is not None:
            self.upload_disabled_due_network = upload_disabled_due_network

    def print_summary(self, reason: Optional[str] = None) -> None:
        if self._printed:
            return
        self._printed = True
        if reason:
            self.exit_reason = reason

        ended_at = datetime.now()
        elapsed = (ended_at - self.started_at).total_seconds()

        print("\n" + "=" * 70)
        print("                    Pipeline 任务统计")
        print("=" * 70)
        print(f"\n退出方式: {self.exit_reason}")
        print(f"开始时间: {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"结束时间: {ended_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"运行时长: {elapsed:.1f} 秒 ({elapsed / 60:.1f} 分钟)")
        print(f"启动命令: {self.command}")

        if self.convert_only:
            print("\n运行模式: convert-only（仅处理本地结果/上传，未运行 solver）")
        if self.repo_id:
            print(f"\nHF Dataset: {self.repo_id}")
            print(f"  URL: https://huggingface.co/datasets/{self.repo_id}")
        elif self.upload_enabled:
            print("\nHF Dataset: 未记录（可能为交互输入前的异常退出）")
        if self.scenario:
            print(f"场景: {self.scenario}")
        if self.range_file:
            print(f"Range 文件: {self.range_file}")

        print(f"\n牌面范围输入: {self.range_input}")
        if self.planned_indices:
            planned_expr = _compress_indices(self.planned_indices)
            print(f"计划求解: {len(self.planned_indices)} 个牌面 ({planned_expr})")
            self._print_board_table(
                "计划牌面",
                [(idx, self.all_boards[idx - 1]) for idx in self.planned_indices],
            )
        else:
            print("计划求解: 0 个牌面")

        if self.total_batches > 0:
            print(f"\nBatch 配置: 共 {self.total_batches} 批，每批最多 {self.batch_size} 个牌面")
            print(f"Batch 进度: {self.current_batch}/{self.total_batches}")
            if self.current_batch > 0:
                batch = self.batches[self.current_batch - 1]
                print(
                    f"当前/最后 Batch #{self.current_batch}: "
                    f"{_compress_indices(batch)} ({len(batch)} 个牌面)"
                )
                self._print_board_table(
                    f"Batch #{self.current_batch} 牌面",
                    [(idx, self.all_boards[idx - 1]) for idx in batch],
                )
            completed_batches = self.current_batch - 1
            if completed_batches > 0:
                completed_indices: List[int] = []
                for batch in self.batches[:completed_batches]:
                    completed_indices.extend(batch)
                print(f"已完成 Batch: 1-{completed_batches} ({len(completed_indices)} 个牌面)")
                self._print_board_table(
                    "已完成 Batch 牌面",
                    [(idx, self.all_boards[idx - 1]) for idx in completed_indices],
                )

        solved_local = self._boards_with_local_results()
        pending_local = [
            (idx, self.all_boards[idx - 1])
            for idx in self.planned_indices
            if idx not in {i for i, _ in solved_local}
        ]
        pending_count = _count_pending_result_files(self.export_format, self.upload_format)
        print(f"\n本地 results ({self.result_dir}): {pending_count} 个待处理文件")
        if solved_local:
            print(f"已有本地结果: {len(solved_local)} 个牌面")
            self._print_board_table("已有本地结果牌面", solved_local)
        if pending_local:
            print(f"尚无本地结果: {len(pending_local)} 个牌面")
            self._print_board_table("尚无本地结果牌面", pending_local)

        print("\n上传状态:")
        if not self.upload_enabled and not self.convert_only:
            print("  上传: 已关闭（中途因网络问题停用，或使用了 --no-upload）")
        elif self.convert_only and self.upload_enabled:
            print("  上传: convert-only 模式")
        elif self.upload_enabled:
            print("  上传: 启用")
        else:
            print("  上传: 未启用 (--no-upload)")
        if self.upload_disabled_due_network:
            print("  网络: 中途上传失败后已切换为仅求解")
        if self.upload_failures > 0:
            print(f"  上传失败次数: {self.upload_failures}")
        if pending_count > 0 and self.repo_id:
            print(
                f"  手动上传: {_manual_upload_command(self.repo_id, self.upload_format)}"
            )

        print("\n" + "=" * 70)

    def _result_board_keys(self) -> Set[str]:
        keys: Set[str] = set()
        if not RESULTS_DIR.is_dir():
            return keys
        patterns: List[str] = []
        if self.export_format == "json":
            patterns.append("*.json")
        else:
            patterns.append("*.parquet")
        if self.upload_format == "parquet" and self.export_format != "parquet":
            patterns.append("*.parquet")
        elif self.upload_format == "json" and self.export_format != "json":
            patterns.append("*.json")
        for pattern in patterns:
            for path in RESULTS_DIR.glob(pattern):
                keys.add(path.stem.casefold())
        return keys

    def _boards_with_local_results(self) -> List[Tuple[int, str]]:
        from auto_run_solver import board_to_filename

        if not self.planned_indices:
            return []
        result_keys = self._result_board_keys()
        solved: List[Tuple[int, str]] = []
        for idx in self.planned_indices:
            board = self.all_boards[idx - 1]
            if board_to_filename(board).casefold() in result_keys:
                solved.append((idx, board))
        return solved

    @staticmethod
    def _print_board_table(title: str, rows: List[Tuple[int, str]], max_rows: int = 30) -> None:
        if not rows:
            return
        print(f"\n{title}:")
        print("-" * 70)
        print(f"{'序号':<8} {'牌面':<20}")
        print("-" * 70)
        shown = rows[:max_rows]
        for idx, board in shown:
            print(f"{idx:<8} {board:<20}")
        if len(rows) > max_rows:
            print(f"... 其余 {len(rows) - max_rows} 个牌面未显示")
        print("-" * 70)


def _run(cmd: list, cwd: Path = None) -> bool:
    """执行命令，返回是否成功"""
    return _run_code(cmd, cwd=cwd) == 0


def _run_code(cmd: list, cwd: Path = None) -> int:
    """执行命令，返回退出码。"""
    cwd = cwd or SCRIPT_DIR
    r = subprocess.run(cmd, cwd=str(cwd))
    return r.returncode


def _batch_report_path(status_file: Optional[Path], batch_number: int) -> Path:
    if status_file:
        return status_file.parent / f"{status_file.stem}.batch-{batch_number}.json"
    return RESULTS_DIR / ".pipeline-reports" / f"solver_pipeline.batch-{batch_number}.json"


def _coerce_index_list(value: Any, allowed: Set[int]) -> List[int]:
    if not isinstance(value, list):
        return []
    indices: List[int] = []
    seen: Set[int] = set()
    for item in value:
        if not isinstance(item, int) or item not in allowed or item in seen:
            continue
        indices.append(item)
        seen.add(item)
    return indices


def _extend_unique(target: List[int], values: List[int]) -> None:
    seen = set(target)
    for value in values:
        if value in seen:
            continue
        target.append(value)
        seen.add(value)


def _read_batch_report(report_path: Path, batch: List[int]) -> Optional[Dict[str, Any]]:
    if not report_path.exists():
        return None
    with open(report_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return None

    allowed = set(batch)
    completed = _coerce_index_list(raw.get("completed_indices"), allowed)
    skipped = _coerce_index_list(raw.get("skipped_indices"), allowed)
    failed = _coerce_index_list(raw.get("failed_indices"), allowed)
    _extend_unique(failed, skipped)
    completed_set = set(completed)
    skipped = [index for index in skipped if index not in completed_set]
    failed = [index for index in failed if index not in completed_set]

    reported = set(completed) | set(failed)
    missing = [index for index in batch if index not in reported]
    _extend_unique(failed, missing)
    abnormal = [index for index in failed if index not in set(skipped)]

    results = raw.get("results")
    if not isinstance(results, list):
        results = []

    return {
        "completed_indices": completed,
        "failed_indices": failed,
        "skipped_indices": skipped,
        "abnormal_indices": abnormal,
        "missing_indices": missing,
        "interrupted": bool(raw.get("interrupted")),
        "results": results,
    }


def _parse_range(expr: str, max_val: int) -> list:
    """解析范围表达式为序号列表"""
    from auto_run_solver import parse_range_expr
    return parse_range_expr(expr, max_value=max_val)


def _compress_indices(indices: list) -> str:
    """将序号列表压缩为范围表达式"""
    from auto_run_solver import compress_indices_to_expr
    return compress_indices_to_expr(indices)


def _get_total_boards() -> int:
    """获取牌面总数"""
    from auto_run_solver import read_cards, CARDS_FILE
    cards_path = CARDS_FILE
    if not cards_path.exists():
        return 1755  # 默认
    boards = read_cards(cards_path)
    return len(boards)


def _get_cards_path(cards_file: str) -> Path:
    return SCRIPT_DIR / "cards" / cards_file


def _read_all_boards(cards_file: str) -> list[str]:
    """读取牌面文件并返回标准化后的牌面字符串列表。"""
    from auto_run_solver import read_cards

    cards_path = _get_cards_path(cards_file)
    boards = read_cards(cards_path)
    return [board for _, board in boards]


def _looks_like_board_names(expr: str) -> bool:
    """判断表达式是否包含牌面名称（含字母 A-T 等牌面字符）而非纯数字范围。"""
    import re
    return bool(re.search(r'[A-Ta-t]', expr))


def _board_filename_lookup_key(fn: str) -> str:
    """与牌面文件比对用的键（大小写不敏感）。"""
    return fn.casefold()


def _resolve_boards_to_indices(expr: str, all_boards: list[str]) -> list[int]:
    """将牌面名称（如 Jc7c5c 或 Jc,7c,5c）解析为 1-based 索引列表。

    支持逗号分隔的多个牌面，例如 "Jc7c5c,AcKc3d"。
    与 cards 文件中的牌面比对时大小写不敏感（如 jc7c5c、JC7C5c 均可）。
    返回排序去重后的索引列表；无法匹配的牌面会打印警告。
    """
    from auto_run_solver import normalize_board, board_to_filename

    filename_to_idx: dict[str, int] = {}
    for i, board in enumerate(all_boards, 1):
        fn = board_to_filename(board)
        key = _board_filename_lookup_key(fn)
        if key in filename_to_idx and filename_to_idx[key] != i:
            print(f"[警告] 牌面文件存在仅大小写不同的重复键: {fn!r}")
        filename_to_idx[key] = i

    indices: list[int] = []
    raw_parts = expr.split(",")

    buf = ""
    board_tokens: list[str] = []
    for part in raw_parts:
        buf = (buf + "," + part) if buf else part
        normalized = normalize_board(buf)
        fn = board_to_filename(normalized)
        card_count = len(fn) // 2
        if card_count >= 3:
            board_tokens.append(buf)
            buf = ""
    if buf:
        board_tokens.append(buf)

    for token in board_tokens:
        normalized = normalize_board(token.strip())
        fn = board_to_filename(normalized)
        idx = filename_to_idx.get(_board_filename_lookup_key(fn))
        if idx is not None:
            indices.append(idx)
        else:
            print(f"[警告] 在牌面文件中未找到: {token.strip()} (标准化: {normalized})")

    return sorted(set(indices))


def _count_json_in_dir(directory: Path) -> int:
    """统计指定目录下的 JSON 文件数量。"""
    if not directory.is_dir():
        return 0
    return len(list(directory.glob("*.json")))


def _count_json() -> int:
    """统计 results 目录下的 JSON 文件数量"""
    return _count_json_in_dir(RESULTS_DIR)


def _count_parquet_in_dir(directory: Path) -> int:
    """统计指定目录下的 Parquet 文件数量。"""
    if not directory.is_dir():
        return 0
    return len(list(directory.glob("*.parquet")))


def _count_parquet() -> int:
    """统计 results 目录下的 Parquet 文件数量"""
    return _count_parquet_in_dir(RESULTS_DIR)


def _count_export_files_in_dir(directory: Path, export_format: str) -> int:
    return _count_json_in_dir(directory) if export_format == "json" else _count_parquet_in_dir(directory)


def _count_export_files(export_format: str) -> int:
    return _count_export_files_in_dir(RESULTS_DIR, export_format)


def _count_upload_files_in_dir(directory: Path, upload_format: str) -> int:
    return _count_json_in_dir(directory) if upload_format == "json" else _count_parquet_in_dir(directory)


def _count_pending_result_files(export_format: str, upload_format: str) -> int:
    if export_format == upload_format:
        return _count_export_files(export_format)
    count = _count_export_files(export_format)
    count += _count_upload_files_in_dir(RESULTS_DIR, upload_format)
    return count


def _scaled_upload_attempt_timeout(timeout_seconds: int, file_count: int) -> int:
    base_timeout = max(1, int(timeout_seconds))
    normalized_count = max(1, int(file_count))
    return max(1, (base_timeout * normalized_count + UPLOAD_TIMEOUT_BASE_FILE_COUNT - 1) // UPLOAD_TIMEOUT_BASE_FILE_COUNT)


def _manual_upload_command(repo_id: str, upload_format: str = DEFAULT_UPLOAD_FORMAT) -> str:
    return (
        f"python upload.py {shlex.quote(str(RESULTS_DIR))} "
        f"--repo-id {shlex.quote(repo_id)} --file-format {shlex.quote(upload_format)}"
    )


def _delete_json_in_dir(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    deleted = 0
    for f in directory.glob("*.json"):
        try:
            f.unlink()
            deleted += 1
        except OSError as e:
            print(f"[警告] 删除 {f.name} 失败: {e}")
    return deleted


def _delete_parquets_in_dir(directory: Path) -> int:
    """删除指定目录下已上传的 parquet 文件，返回删除数量。"""
    if not directory.is_dir():
        return 0
    deleted = 0
    for f in directory.glob("*.parquet"):
        try:
            f.unlink()
            deleted += 1
        except OSError as e:
            print(f"[警告] 无法删除 {f.name}: {e}")
    return deleted


def _delete_parquets() -> int:
    """删除 results 目录下已上传的 parquet 文件，返回删除数量。"""
    return _delete_parquets_in_dir(RESULTS_DIR)



def _process_artifacts_in_dir(
    target_dir: Path,
    upload: bool,
    repo_id: Optional[str] = None,
    upload_format: str = DEFAULT_UPLOAD_FORMAT,
    upload_attempt_timeout: int = DEFAULT_UPLOAD_ATTEMPT_TIMEOUT_SECONDS,
) -> tuple[bool, int]:
    json_count = _count_json_in_dir(target_dir)
    ok = True

    if upload_format == "parquet" and json_count > 0:
        if not _ensure_pyarrow():
            return False, 1
        ok = _run([sys.executable, str(UPLOAD_DIR / "batch_json_to_parquet.py"), str(target_dir)])
        if not ok:
            return False, 1

    ready_count = _count_upload_files_in_dir(target_dir, upload_format)
    if ready_count == 0:
        return ok, 0

    if ok and upload:
        if not repo_id:
            print("[Error] Missing Hugging Face repo_id")
            return False, 1
        effective_timeout = _scaled_upload_attempt_timeout(upload_attempt_timeout, ready_count)
        print(
            f"[Upload] Attempt timeout: {effective_timeout}s "
            f"({ready_count} {upload_format} files; base {upload_attempt_timeout}s/{UPLOAD_TIMEOUT_BASE_FILE_COUNT} files)"
        )
        upload_code = _run_code([
            sys.executable,
            str(UPLOAD_DIR / "upload_to_hf.py"),
            str(target_dir),
            "--repo-id",
            repo_id,
            "--file-format",
            upload_format,
            "--attempt-timeout",
            str(effective_timeout),
            "--attempt-timeout-mode",
            "fixed",
            "--max-retries",
            "1",
        ])
        ok = upload_code == 0
        return ok, upload_code
    return ok, 0


def _process_artifacts(
    upload: bool,
    repo_id: Optional[str] = None,
    upload_format: str = DEFAULT_UPLOAD_FORMAT,
    upload_attempt_timeout: int = DEFAULT_UPLOAD_ATTEMPT_TIMEOUT_SECONDS,
) -> tuple[bool, int]:
    return _process_artifacts_in_dir(
        RESULTS_DIR,
        upload,
        repo_id=repo_id,
        upload_format=upload_format,
        upload_attempt_timeout=upload_attempt_timeout,
    )


def _dataset_name_from_repo_id(repo_id: str) -> str:
    """HF dataset 名（repo_id 最后一段），如 user/sia-12-sod-30 -> sia-12-sod-30"""
    return repo_id.split("/")[-1].strip()


def _repo_id_to_range_filename(repo_id: str) -> str:
    """从 repo_id 得到对应的 range 文件名。

    E.g. "Tsumugii/sia-100bb" -> "sia-100bb.txt"
         "user/sia-12-sod-30" -> "sia-12-sod-30.txt"
    """
    name = _dataset_name_from_repo_id(repo_id)
    if name.endswith(".txt"):
        return name
    return f"{name}.txt"


def _infer_scenario_subdir_for_dataset(dataset_name: str) -> Optional[str]:
    """根据 dataset / 文件名推断应落在 ranges/<subdir>/ 下的子目录。"""
    return infer_scenario_from_name(dataset_name)


def _list_range_txt_for_errors() -> list[str]:
    """用于报错时列出可选的 range 文件（根目录 + 子目录）。"""
    out: list[str] = []
    if RANGES_DIR.is_dir():
        for f in sorted(RANGES_DIR.glob("*.txt")):
            out.append(f.name)
        for sub in SCENARIO_SUBDIRS:
            d = RANGES_DIR / sub
            if d.is_dir():
                for f in sorted(d.glob("*.txt")):
                    out.append(f"{sub}/{f.name}")
    return out


def _find_range_file(repo_id: str) -> Optional[Path]:
    """在 ranges/ 根目录及各场景子目录中查找与 repo dataset 名一致的 .txt。"""
    filename = _repo_id_to_range_filename(repo_id)
    lower = filename.casefold()
    dataset_name = _dataset_name_from_repo_id(repo_id)

    # 1) 根目录 ranges/<name>.txt
    p = RANGES_DIR / filename
    if p.is_file():
        return p
    if RANGES_DIR.is_dir():
        for f in RANGES_DIR.glob("*.txt"):
            if f.is_file() and f.name.casefold() == lower:
                return f

    # 2) 按命名推断子目录后精确路径
    sub = _infer_scenario_subdir_for_dataset(dataset_name)
    if sub:
        p = RANGES_DIR / sub / filename
        if p.is_file():
            return p
        d = RANGES_DIR / sub
        if d.is_dir():
            for f in d.glob("*.txt"):
                if f.is_file() and f.name.casefold() == lower:
                    return f

    # 3) 未推断出子目录时，在标准子目录中按文件名搜索
    for scenario in SCENARIO_SUBDIRS:
        d = RANGES_DIR / scenario
        if not d.is_dir():
            continue
        p = d / filename
        if p.is_file():
            return p
        for f in d.glob("*.txt"):
            if f.is_file() and f.name.casefold() == lower:
                return f

    return None


def _load_ranges_from_file(range_file: Path) -> tuple[str, str]:
    """Load OOP_RANGE and IP_RANGE from a range config file."""
    oop, ip = "", ""
    with open(range_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip().upper()
            val = val.strip().strip('"\'')
            if key == "OOP_RANGE":
                oop = val
            elif key == "IP_RANGE":
                ip = val
    if not oop or not ip:
        missing = []
        if not oop:
            missing.append("OOP_RANGE")
        if not ip:
            missing.append("IP_RANGE")
        raise ValueError(f"Range file {range_file.name} is missing: {', '.join(missing)}")
    return oop, ip


def _normalize_range_str(range_str: str) -> str:
    return "".join(range_str.split())


def _parse_cards_solver_config_ranges(config_path: Path) -> tuple[str, str]:
    """从 cards/<board>.txt 读取 set_range_oop / set_range_ip。"""
    oop, ip = "", ""
    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("set_range_oop"):
                oop = line[len("set_range_oop"):].strip()
            elif line.startswith("set_range_ip"):
                ip = line[len("set_range_ip"):].strip()
    return oop, ip


def _find_range_file_by_ranges(oop: str, ip: str) -> Optional[Path]:
    """在 ranges/ 下查找与给定 OOP/IP range 完全一致的配置文件。"""
    target_oop = _normalize_range_str(oop)
    target_ip = _normalize_range_str(ip)
    if not target_oop or not target_ip:
        return None
    for path in RANGES_DIR.rglob("*.txt"):
        if not path.is_file():
            continue
        try:
            file_oop, file_ip = _load_ranges_from_file(path)
        except ValueError:
            continue
        if (
            _normalize_range_str(file_oop) == target_oop
            and _normalize_range_str(file_ip) == target_ip
        ):
            return path
    return None


def _unuploaded_result_stems() -> list[str]:
    stems: set[str] = set()
    if not RESULTS_DIR.is_dir():
        return []
    for pattern in ("*.parquet", "*.json"):
        for path in RESULTS_DIR.glob(pattern):
            stems.add(path.stem)
    return sorted(stems)


def _current_dataset_label(repo_id: Optional[str], range_file: Optional[Path]) -> str:
    if repo_id:
        return repo_id
    if range_file is not None:
        return range_file.stem
    return "未知"


def _warn_unuploaded_results_range_mismatch(
    current_oop: str,
    current_ip: str,
    *,
    repo_id: Optional[str] = None,
    range_file: Optional[Path] = None,
) -> None:
    """检测当前结果目录中未上传结果是否属于与当前任务不同的 range。"""
    stems = _unuploaded_result_stems()
    if not stems:
        return

    from check_missing import parse_repo_id

    current_oop_n = _normalize_range_str(current_oop)
    current_ip_n = _normalize_range_str(current_ip)
    mismatches: dict[str, list[str]] = {}
    matched_count = 0

    for stem in stems:
        config_path = CARDS_DIR / f"{stem}.txt"
        if not config_path.is_file():
            print(
                f"[警告] {RESULTS_DIR}/{stem}.parquet 无对应 cards/{stem}.txt，"
                "无法校验 range，请确认是否应手动上传"
            )
            continue

        file_oop, file_ip = _parse_cards_solver_config_ranges(config_path)
        if not file_oop or not file_ip:
            print(
                f"[警告] cards/{stem}.txt 缺少 set_range_oop/set_range_ip，"
                "无法校验 range"
            )
            continue

        if (
            _normalize_range_str(file_oop) == current_oop_n
            and _normalize_range_str(file_ip) == current_ip_n
        ):
            matched_count += 1
            continue

        matched_range = _find_range_file_by_ranges(file_oop, file_ip)
        if matched_range is not None:
            dataset_name = matched_range.stem
        else:
            dataset_name = "未知 dataset"
        mismatches.setdefault(dataset_name, []).append(stem)

    if not mismatches:
        if matched_count == len(stems):
            dataset_label = _current_dataset_label(repo_id, range_file)
            print(
                f"[Range Check] {RESULTS_DIR} 中 {matched_count} 个未上传结果与当前 range 全部一致，"
                f"对应 dataset: {dataset_label}"
            )
        return

    total = sum(len(v) for v in mismatches.values())
    current_dataset = _current_dataset_label(repo_id, range_file)
    print("\n" + "=" * 60)
    print(f"[Range Mismatch] {RESULTS_DIR} 中有 {total} 个未上传结果与当前 range 不一致")
    print("=" * 60)
    print(f"当前目标任务 dataset: {current_dataset}")
    print("当前 range 文件对应 OOP/IP 与这些结果求解时使用的 range 不同。")
    print("请勿将它们上传到当前 dataset；请先手动上传到各自对应的 dataset：\n")

    for dataset_name, boards in sorted(mismatches.items()):
        preview = ", ".join(boards[:8])
        suffix = f" ... (+{len(boards) - 8})" if len(boards) > 8 else ""
        print(f"  dataset: {dataset_name}")
        print(f"  牌面 ({len(boards)}): {preview}{suffix}")
        if dataset_name != "未知 dataset":
            repo_hint = parse_repo_id(dataset_name)
            print(f"  手动上传: {_manual_upload_command(repo_hint)}")
        else:
            print(
                f"  手动上传: 未在 ranges/ 找到匹配 range，"
                f"请根据 cards/{{board}}.txt 中的 set_range_* 自行确认 dataset"
            )
        print()


def _prompt_repo_id() -> tuple[str, Path]:
    """交互式获取 HF dataset repo_id，并验证 ranges/ 下存在匹配的 range 文件。

    Returns:
        (repo_id, range_file_path) — 验证通过的 repo_id 及其对应的 range 文件路径。
    """
    env_repo_id = os.environ.get("HF_REPO_ID")
    if env_repo_id:
        repo_id = env_repo_id.strip()
        print(f"[HF] 检测到 HF_REPO_ID={repo_id}")
        range_file = _find_range_file(repo_id)
        if range_file:
            print(f"[Range] 已匹配 range 文件: {range_file.name}")
            return repo_id, range_file
        print(f"[警告] 未找到 repo_id '{repo_id}' 对应的 range 文件: "
              f"{_repo_id_to_range_filename(repo_id)}")
        print(f"  ranges/ 下可用: {_list_range_txt_for_errors()}")

    while True:
        print("\n[提示] 请输入要上传的 Hugging Face dataset repo_id")
        print("  例如: username/dataset-name")
        print("  输入 q / quit / exit 退出")
        user_input = input("  repo_id: ").strip()

        if user_input.casefold() in ("q", "quit", "exit", ""):
            print("[退出] 用户取消")
            sys.exit(0)

        range_file = _find_range_file(user_input)
        if range_file:
            print(f"[Range] 已匹配 range 文件: {range_file.name}")
            return user_input, range_file

        expected = _repo_id_to_range_filename(user_input)
        available = _list_range_txt_for_errors()
        print(f"[警告] 未找到 '{expected}'（已搜索 ranges/ 根目录与所有场景子目录）")
        print(f"  可用文件: {available}")
        print("  请重新输入或输入 q/quit/exit 退出")


def _normalize_repo_id(repo_id: str) -> str:
    """支持从 URL、owner/repo 或仅 dataset 名解析标准 repo_id。"""
    from check_missing import default_hf_namespace, parse_repo_id

    original = repo_id.strip()
    normalized = parse_repo_id(original)
    if "/" not in original and "huggingface.co" not in original.casefold():
        print(f"[HF] Repo id: {original} -> {normalized} (namespace: {default_hf_namespace()})")
    return normalized


def _local_result_board_keys() -> Set[str]:
    """当前结果目录下已有导出文件的牌面键（大小写不敏感）。"""
    keys: Set[str] = set()
    if not RESULTS_DIR.is_dir():
        return keys
    for pattern in ("*.json", "*.parquet"):
        for path in RESULTS_DIR.glob(pattern):
            keys.add(path.stem.casefold())
    return keys


def _filter_requested_indices_by_hf(
    repo_id: str,
    requested_indices: list[int],
    cards_file: str,
    *,
    skip_local_results: bool = True,
) -> tuple[list[int], int, int, int, list[int]]:
    """
    根据 Hugging Face dataset（及可选的本地 results）过滤待求解序号。

    Returns:
        (remaining_indices, total_repo_existing, skipped_in_requested, total_boards, skipped_indices)
    """
    from check_missing import board_file_key, list_boards_in_hf_dataset

    all_boards = _read_all_boards(cards_file)
    boards_in_hf = list_boards_in_hf_dataset(repo_id)
    boards_local = _local_result_board_keys() if skip_local_results else set()

    requested_set = set(requested_indices)
    remaining_indices: list[int] = []
    skipped_indices: list[int] = []
    total_repo_existing = 0
    skipped_in_requested = 0

    for idx, board in enumerate(all_boards, start=1):
        board_key = board_file_key(board)
        exists_in_repo = board_key in boards_in_hf
        exists_local = board_key in boards_local
        if exists_in_repo:
            total_repo_existing += 1
        if idx in requested_set:
            if exists_in_repo or exists_local:
                skipped_in_requested += 1
                skipped_indices.append(idx)
            else:
                remaining_indices.append(idx)

    return (
        remaining_indices,
        total_repo_existing,
        skipped_in_requested,
        len(all_boards),
        skipped_indices,
    )


def _print_hf_resume_info(
    all_boards: list[str],
    remaining_indices: list[int],
    skipped_indices: list[int],
    batch_size: int,
) -> None:
    """打印 HF 扫描后的续跑说明。"""
    if not remaining_indices:
        return

    first_batch = remaining_indices[:batch_size]
    first_expr = _compress_indices(first_batch)
    first_idx = remaining_indices[0]
    first_board = all_boards[first_idx - 1]

    print(f"Resume From Index: {first_idx} ({first_board})")
    print(f"First Batch: {first_expr}")

    if skipped_indices:
        skipped_expr = _compress_indices(skipped_indices)
        if len(skipped_expr) > 120:
            preview = _compress_indices(skipped_indices[:20])
            print(f"Skipped In HF/local: {len(skipped_indices)} boards, e.g. {preview} ...")
        else:
            print(f"Skipped In HF/local: {skipped_expr}")

        if remaining_indices[0] <= 10 and skipped_indices:
            max_skipped = max(skipped_indices)
            if max_skipped > remaining_indices[0]:
                print(
                    "[Info] HF/local 已有牌面并非 cards.txt 的前 N 个连续序号，"
                    "将从首个缺失序号继续（不是从最大已完成序号+1）。"
                )



def _ensure_pyarrow() -> bool:
    """确保 pyarrow 已安装，未安装则自动 pip install。返回是否可用。"""
    try:
        import pyarrow
        return True
    except ImportError:
        print("[转换] 未检测到 pyarrow，正在自动安装...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "pyarrow"],
            cwd=str(SCRIPT_DIR),
        )
        if r.returncode != 0:
            print("[错误] 安装 pyarrow 失败，请手动运行: pip install pyarrow")
            return False
        print("[转换] pyarrow 安装完成")
        return True


def _ensure_huggingface_hub() -> bool:
    """确保 huggingface_hub 已安装，未安装则自动 pip install。返回是否可用。"""
    try:
        from huggingface_hub import get_token
        return True
    except ImportError:
        print("[HF] 未检测到 huggingface_hub，正在自动安装...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"],
            cwd=str(SCRIPT_DIR),
        )
        if r.returncode != 0:
            print("[错误] 安装 huggingface_hub 失败，请手动运行: pip install huggingface_hub")
            return False
        print("[HF] 安装完成")
        return True


def _ensure_hf_logged_in() -> bool:
    """检查 HF 登录状态，未登录则尝试用 HF_TOKEN 登录。返回是否已登录。"""
    if not _ensure_huggingface_hub():
        return False
    try:
        from huggingface_hub import get_token, login, HfApi
    except ImportError:
        print("[错误] 无法导入 huggingface_hub")
        return False

    token = get_token()
    if not token:
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
        if token:
            print("[HF] 检测到 HF_TOKEN，正在登录...")
            try:
                login(token=token)
            except Exception as e:
                print(f"[错误] HF 登录失败: {e}")
                return False
        else:
            print("[提示] 未登录 Hugging Face，且未设置 HF_TOKEN 环境变量")
            print("  请在此输入您的 Hugging Face Token（或按 Enter 退出）:")
            token = input("  HF Token: ").strip()
            if not token:
                print("[错误] 未提供 Token，退出")
                return False
            print("[HF] 正在使用输入的 Token 登录...")
            try:
                login(token=token)
            except Exception as e:
                print(f"[错误] HF 登录失败: {e}")
                return False

    try:
        api = HfApi(token=token)
        info = api.whoami()
        print(f"[HF] 已登录: {info.get('name', 'unknown')}")
    except Exception as e:
        print(f"[警告] 无法验证 HF 登录: {e}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Solver → JSON/Parquet → Hugging Face 自动化流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("range", nargs="?", default="all",
                        help="序号范围，如 1-20 或 1,5,10,15,20 或 all；默认 all")
    parser.add_argument("--batch-size", "-b", type=int, default=5,
                        help="导出产物积累到此数时触发后续处理/上传（默认 5）")
    parser.add_argument("--no-upload", action="store_true",
                        help="只求解+转换，不上传到 HF")
    parser.add_argument("--convert-only", action="store_true",
                        help="仅处理已有结果并上传，不跑 solver")
    parser.add_argument("--repo-id", type=str, default=None,
                        help="目标 Hugging Face dataset repo_id；不传则启动时交互输入")
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Setting/场景 ID；与 --range-file 联用时也作为 ranges/<scenario>/ 子目录",
    )
    setting_source = parser.add_mutually_exclusive_group()
    setting_source.add_argument(
        "--setting-snapshot",
        type=str,
        default=None,
        help=argparse.SUPPRESS,
    )
    setting_source.add_argument(
        "--setting-file",
        type=str,
        default=None,
        help="Job-scoped Setting Library JSON file supplied by Server Monitor",
    )
    parser.add_argument(
        "--range-file",
        type=str,
        default=None,
        help="场景目录内文件名，如 sia-12-sod-30.txt（与 --scenario 搭配；优先于 repo_id 自动匹配）",
    )
    parser.add_argument(
        "--range-path",
        type=str,
        default=None,
        help="任意 range 配置文件路径；优先于 --scenario/--range-file 与 repo_id 自动匹配",
    )
    parser.add_argument(
        "--oop-range",
        type=str,
        default=None,
        help="直接传入 OOP range；必须与 --ip-range 同时使用",
    )
    parser.add_argument(
        "--ip-range",
        type=str,
        default=None,
        help="直接传入 IP range；必须与 --oop-range 同时使用",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="仅预览，不执行")
    # 透传给 auto_run_solver
    parser.add_argument("--file", type=str, default="cards.txt")
    parser.add_argument("--thread-num", type=int, default=-1)
    parser.add_argument("--use-isomorphism", type=int, choices=[0, 1], default=1)
    parser.add_argument("--max-iteration", type=int, default=300)
    parser.add_argument(
        "--estimate-memory",
        action="store_true",
        help="在 solver build_tree 后执行 estimate_memory 并输出内存估算（默认关闭）",
    )
    parser.add_argument(
        "--stall-timeout",
        type=int,
        default=None,
        help="同一轮 exploitability 输出阶段停滞判定秒数（默认: 使用 auto_run_solver.py 中的默认值）",
    )
    parser.add_argument(
        "--no-output-timeout",
        type=int,
        default=None,
        help="连续无输出停滞判定秒数（默认: 使用 auto_run_solver.py 中的默认值）",
    )
    parser.add_argument(
        "--export-format",
        "--dump-format",
        dest="export_format",
        type=str,
        default=DEFAULT_EXPORT_FORMAT,
        choices=SUPPORTED_EXPORT_FORMATS,
        help=f"solver 导出格式（默认: {DEFAULT_EXPORT_FORMAT}）"
    )
    parser.add_argument(
        "--upload-format",
        type=str,
        default=DEFAULT_UPLOAD_FORMAT,
        choices=SUPPORTED_UPLOAD_FORMATS,
        help=f"最终上传到 Hugging Face 的格式（默认: {DEFAULT_UPLOAD_FORMAT}）",
    )
    parser.add_argument(
        "--upload-attempt-timeout",
        type=int,
        default=int(os.environ.get("HF_UPLOAD_ATTEMPT_TIMEOUT", DEFAULT_UPLOAD_ATTEMPT_TIMEOUT_SECONDS)),
        help="HF 上传超时基准秒数，按待上传文件数相对 5 个文件线性缩放（默认: 120，可用 HF_UPLOAD_ATTEMPT_TIMEOUT 覆盖）",
    )
    parser.add_argument(
        "--result-path",
        type=str,
        default=None,
        help="结果输出目录（默认: solver/results；相对路径按 solver 根目录解析）",
    )
    parser.add_argument(
        "--status-file",
        type=str,
        default=None,
        help=f"pipeline 状态 JSON 输出路径（默认: {DEFAULT_PIPELINE_STATUS_FILE}，可用 PIPELINE_STATUS_FILE 覆盖）",
    )
    parser.add_argument(
        "--no-status-file",
        action="store_true",
        help="不写入 pipeline 状态文件",
    )
    args = parser.parse_args()
    setting_file_path: Optional[Path] = None
    try:
        if args.setting_file:
            setting_file_path = Path(args.setting_file).expanduser()
            if not setting_file_path.is_absolute():
                setting_file_path = SCRIPT_DIR / setting_file_path
            setting_file_path = setting_file_path.resolve()
            setting = register_solver_setting_file(
                setting_file_path,
                SCENARIO_CONFIG,
                SCENARIO_DEFAULTS,
                expected_id=args.scenario,
            )
            if args.scenario is None:
                args.scenario = setting["id"]
        elif args.setting_snapshot:
            setting = register_solver_setting_snapshot(
                args.setting_snapshot,
                SCENARIO_CONFIG,
                SCENARIO_DEFAULTS,
                expected_id=args.scenario,
            )
            if args.scenario is None:
                args.scenario = setting["id"]
        if args.scenario and (
            args.scenario not in SCENARIO_CONFIG or args.scenario not in SCENARIO_DEFAULTS
        ):
            available = ", ".join(sorted(SCENARIO_CONFIG))
            raise ValueError(f"Unknown Setting/scenario {args.scenario!r}. Available: {available}")
    except ValueError as exc:
        parser.error(str(exc))

    if not args.no_upload and not args.convert_only:
        if args.upload_format == "json" and args.export_format != "json":
            print("[Error] --upload-format json requires --export-format json")
            sys.exit(1)

    global RESULTS_DIR
    RESULTS_DIR = _resolve_result_dir(args.result_path)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        all_boards = _read_all_boards(args.file)
        total = len(all_boards)
    except Exception as e:
        print(f"[Error] Unable to read cards file: {e}")
        sys.exit(1)
    if args.range.lower() == "all":
        indices = list(range(1, total + 1))
    elif _looks_like_board_names(args.range):
        indices = _resolve_boards_to_indices(args.range, all_boards)
    else:
        indices = _parse_range(args.range, total)
    if not indices:
        print("[错误] 没有有效的序号或牌面")
        sys.exit(1)

    batch_size = max(1, args.batch_size)
    batches = [indices[i:i + batch_size] for i in range(0, len(indices), batch_size)]
    last_batch_size = len(batches[-1]) if batches else 0
    has_remainder = last_batch_size > 0 and last_batch_size < batch_size and len(batches) > 1

    print("=" * 60)
    print("Automatic Pipeline: Solver Export → Format Processing → Hugging Face")
    print("=" * 60)
    print(f"Requested Range: {args.range} ({len(indices)} boards before HF/local skip)")
    print(f"Export Format: {args.export_format}")
    print(f"Upload Format: {args.upload_format}")
    per_file_timeout = _scaled_upload_attempt_timeout(args.upload_attempt_timeout, 1)
    current_batch_timeout = _scaled_upload_attempt_timeout(args.upload_attempt_timeout, batch_size)
    print(
        f"Upload Attempt Timeout: {per_file_timeout}s/file "
        f"(base {args.upload_attempt_timeout}s/{UPLOAD_TIMEOUT_BASE_FILE_COUNT} files; "
        f"{current_batch_timeout}s for batch size {batch_size})"
    )
    print(f"Estimate Memory: {'enabled' if args.estimate_memory else 'disabled'}")
    print(f"Trigger Condition: When export artifacts count >= {batch_size}, move to background for processing and uploading")
    if sys.platform == "win32" and args.export_format == "json" and args.upload_format == "parquet":
        print("  (Windows solver exports JSON; pipeline will convert JSON to Parquet before upload)")
    if has_remainder:
        print(f"  (Last batch has {last_batch_size} boards; any remaining artifacts will be processed at the end)")
    print(f"Results Directory: {RESULTS_DIR}")
    if args.dry_run:
        print("\n[DRY RUN] Only preview, no execution")
        for i, batch in enumerate(batches, 1):
            expr = _compress_indices(batch)
            n = len(batch)
            suffix = f" ({n} boards)" if has_remainder and i == len(batches) else ""
            print(f"  Batch {i}: {expr}{suffix}")
        sys.exit(0)

    repo_id = args.repo_id.strip() if args.repo_id else None
    range_file: Optional[Path] = None
    inline_oop_range = args.oop_range.strip() if args.oop_range else None
    inline_ip_range = args.ip_range.strip() if args.ip_range else None
    current_oop: Optional[str] = None
    current_ip: Optional[str] = None

    if bool(inline_oop_range) != bool(inline_ip_range):
        print("[错误] --oop-range 与 --ip-range 必须同时提供")
        sys.exit(1)

    if inline_oop_range and inline_ip_range:
        current_oop, current_ip = inline_oop_range, inline_ip_range
    elif args.range_path:
        range_file = _resolve_cli_range_path(args.range_path)
        if not range_file.is_file():
            print(f"[错误] 找不到显式指定的 range 文件: {range_file}")
            sys.exit(1)

    if range_file is None and current_oop is None and args.scenario and args.range_file:
        cand = RANGES_DIR / args.scenario / args.range_file
        if cand.is_file():
            range_file = cand.resolve()
        else:
            alt = RANGES_DIR / args.range_file
            if alt.is_file():
                range_file = alt.resolve()
            else:
                print(f"[错误] 找不到显式指定的 range 文件: {cand}")
                print(f"  亦未在 ranges/ 根目录找到: {alt}")
                print(f"  ranges/ 下可用: {_list_range_txt_for_errors()}")
                sys.exit(1)

    if repo_id:
        try:
            repo_id = _normalize_repo_id(repo_id)
        except Exception as e:
            print(f"[Error] Invalid repo_id: {e}")
            sys.exit(1)
        range_file = range_file or (None if current_oop is not None else _find_range_file(repo_id))
        if not range_file and current_oop is None:
            expected = _repo_id_to_range_filename(repo_id)
            print(f"[错误] 未找到 repo_id '{repo_id}' 对应的 range 文件: {expected}")
            print(f"  （已搜索 ranges/ 根目录与所有场景子目录）")
            print(f"  ranges/ 下可用: {_list_range_txt_for_errors()}")
            sys.exit(1)

    # 需要上传时，先确认 repo_id 并检查 HF 登录
    if not args.no_upload:
        if not repo_id:
            repo_id, range_file = _prompt_repo_id()
        try:
            repo_id = _normalize_repo_id(repo_id)
        except Exception as e:
            print(f"[Error] Invalid repo_id: {e}")
            sys.exit(1)

        print(f"[HF] Target Repository: https://huggingface.co/datasets/{repo_id}")
        if not _ensure_hf_logged_in():
            print("[Error] Unable to upload: Please login to HF or set HF_TOKEN")
            sys.exit(1)

    if not args.convert_only:
        if current_oop is None and (range_file is None or not range_file.is_file()):
            print("[错误] 运行 solver 需要 range：请使用 --repo-id（HF dataset 名应对应 ranges 下某 .txt 基名），")
            print("  或指定 --range-path / --oop-range + --ip-range / --scenario + --range-file。")
            print(f"  ranges/ 下可用: {_list_range_txt_for_errors()}")
            sys.exit(1)

    if range_file and range_file.is_file():
        try:
            current_oop, current_ip = _load_ranges_from_file(range_file)
            print(f"[Range] File: {range_file.name}")
            print(f"  OOP: {current_oop[:80]}{'...' if len(current_oop) > 80 else ''}")
            print(f"  IP:  {current_ip[:80]}{'...' if len(current_ip) > 80 else ''}")
            _warn_unuploaded_results_range_mismatch(
                current_oop,
                current_ip,
                repo_id=repo_id,
                range_file=range_file,
            )
        except Exception as e:
            print(f"[错误] 读取 range 文件失败: {e}")
            sys.exit(1)
    elif current_oop and current_ip:
        print("[Range] Source: command line OOP/IP range")
        print(f"  OOP: {current_oop[:80]}{'...' if len(current_oop) > 80 else ''}")
        print(f"  IP:  {current_ip[:80]}{'...' if len(current_ip) > 80 else ''}")
        _warn_unuploaded_results_range_mismatch(
            current_oop,
            current_ip,
            repo_id=repo_id,
            range_file=None,
        )

    selected_scenario = args.scenario or (infer_scenario_from_range_path(range_file) if range_file else None)
    if not args.convert_only and not selected_scenario:
        print("[错误] 使用 --oop-range/--ip-range 时必须同时指定 --scenario")
        sys.exit(1)
    if selected_scenario and (
        selected_scenario not in SCENARIO_CONFIG or selected_scenario not in SCENARIO_DEFAULTS
    ):
        print(f"[错误] 未知 Setting/场景: {selected_scenario}")
        sys.exit(1)

    if repo_id and not args.convert_only:
        try:
            requested_count = len(indices)
            indices, repo_existing_count, skipped_in_requested, total_boards, skipped_indices = (
                _filter_requested_indices_by_hf(
                    repo_id=repo_id,
                    requested_indices=indices,
                    cards_file=args.file,
                )
            )
        except Exception as e:
            print(f"[Error] Failed to check existing boards in HF dataset: {e}")
            sys.exit(1)

        batch_size = max(1, args.batch_size)
        batches = [indices[i:i + batch_size] for i in range(0, len(indices), batch_size)]
        last_batch_size = len(batches[-1]) if batches else 0
        has_remainder = last_batch_size > 0 and last_batch_size < batch_size and len(batches) > 1

        print("\n" + "=" * 60)
        print("[Check Missing] Hugging Face dataset scan completed")
        print("=" * 60)
        print(f"Dataset Existing: {repo_existing_count}/{total_boards}")
        print(f"Requested Indices: {requested_count}")
        print(f"Already Done In Request: {skipped_in_requested}")
        print(f"Remaining To Solve: {len(indices)}")
        _print_hf_resume_info(all_boards, indices, skipped_indices, batch_size)
        if not indices:
            print("[Info] Requested range is already complete in the target dataset")
        print(f"Total Tasks To Solve: {len(indices)} boards")
        print(f"Solver Batches: {len(batches)}, max {batch_size} per batch")
        if not indices:
            pass
        elif batches:
            print(f"First Solver Batch: {_compress_indices(batches[0])}")
    else:
        print(f"Total Tasks To Solve: {len(indices)} boards")
        print(f"Solver Batches: {len(batches)}, max {batch_size} per batch")
        if batches:
            print(f"First Solver Batch: {_compress_indices(batches[0])}")

    if not indices and repo_id and not args.convert_only:
        print("[Info] Nothing left to solve; exiting")
        sys.exit(0)

    do_upload = not args.no_upload
    upload_failures = 0
    upload_disabled_due_network = False
    completed_indices: List[int] = []
    failed_indices: List[int] = []
    skipped_indices: List[int] = []
    tracker: Optional[PipelineStatusTracker] = None
    status_file: Optional[Path] = None
    summary = PipelineRunSummary()
    summary.configure(
        range_input=args.range,
        repo_id=repo_id,
        scenario=selected_scenario,
        range_file=range_file,
        all_boards=all_boards,
        planned_indices=indices,
        batches=batches,
        batch_size=batch_size,
        export_format=args.export_format,
        upload_format=args.upload_format,
        result_dir=RESULTS_DIR,
        upload_enabled=do_upload,
        convert_only=args.convert_only,
    )
    atexit.register(summary.print_summary)

    def _finish_pipeline(status: str, reason: str, **tracker_fields: Any) -> None:
        summary.set_upload_state(
            upload_enabled=do_upload,
            upload_failures=upload_failures,
            upload_disabled_due_network=upload_disabled_due_network,
        )
        summary.print_summary(reason)
        if tracker:
            tracker.finalize(status, **tracker_fields)

    if not args.no_status_file:
        status_file = _resolve_status_file(args.status_file)
        tracker = PipelineStatusTracker(status_file)
        try:
            rel_range = str(range_file.resolve().relative_to(SCRIPT_DIR)) if range_file else None
        except ValueError:
            rel_range = range_file.name if range_file else None
        tracker.update(
            status="running",
            repo_id=repo_id,
            dataset_name=_dataset_name_from_repo_id(repo_id) if repo_id else None,
            scenario=selected_scenario,
            range_file=rel_range,
            range_source="inline" if current_oop and current_ip and range_file is None else "file",
            upload_enabled=do_upload,
            convert_only=args.convert_only,
            no_upload=args.no_upload,
            total_tasks=len(indices),
            assigned_indices=indices,
            completed_indices=completed_indices,
            failed_indices=failed_indices,
            skipped_indices=skipped_indices,
            completed_count=0,
            failed_count=0,
            skipped_count=0,
            total_batches=len(batches),
            current_batch=0,
            export_format=args.export_format,
            upload_format=args.upload_format,
            result_path=str(RESULTS_DIR),
            cards_file=args.file,
            batch_size=batch_size,
            command=" ".join(sys.argv),
        )
        print(f"[Status] 外部可读状态文件: {status_file}")

    try:
        if args.convert_only:
            print("\n[Mode] Only process existing results and upload (no solver)")
            if _count_json() == 0 and _count_parquet() == 0:
                print("[Info] results directory has no JSON or Parquet files")
                _finish_pipeline("completed", "正常结束", message="no artifacts to process")
                sys.exit(0)
            ok, _ = _process_artifacts(
                do_upload,
                repo_id=repo_id,
                upload_format=args.upload_format,
                upload_attempt_timeout=args.upload_attempt_timeout,
            )
            if not ok:
                remaining_files = _count_pending_result_files(args.export_format, args.upload_format)
                print("[Completed] Final upload failed")
                print(
                    f"本地 {RESULTS_DIR} 下有 {remaining_files} 个求解结果，请检查当前网络环境后，"
                    f"尝试使用命令 {_manual_upload_command(repo_id, args.upload_format)} 进行手动上传"
                )
                _finish_pipeline(
                    "completed_with_upload_failures",
                    "正常结束（上传失败）",
                    mode="convert_only",
                    upload_failures=1,
                    remaining_export_files=remaining_files,
                )
                sys.exit(1)
            _finish_pipeline("completed", "正常结束", mode="convert_only")
            sys.exit(0)

        for i, batch in enumerate(batches, 1):
            expr = _compress_indices(batch)
            summary.set_batch(i)
            if tracker:
                tracker.update(
                    current_batch=i,
                    batch_expr=expr,
                    batch_size_current=len(batch),
                )
            print(f"\n{'='*60}")
            print(f"[Batch {i}/{len(batches)}] Solving: {expr}")
            print("=" * 60)
            report_path = _batch_report_path(status_file, i)
            try:
                report_path.unlink(missing_ok=True)
            except OSError:
                pass

            solver_cmd = [
                sys.executable, str(SCRIPT_DIR / "auto_run_solver.py"),
                expr,
                "--file", args.file,
                "--scenario", selected_scenario,
                "--thread-num", str(args.thread_num),
                "--use-isomorphism", str(args.use_isomorphism),
                "--max-iteration", str(args.max_iteration),
                "--dump-format", args.export_format,
                "--result-path", str(RESULTS_DIR),
                "--report-json", str(report_path),
            ]
            if setting_file_path is not None:
                solver_cmd.extend(["--setting-file", str(setting_file_path)])
            else:
                solver_cmd.extend([
                    "--setting-snapshot",
                    snapshot_for_scenario(
                        selected_scenario,
                        SCENARIO_CONFIG,
                        SCENARIO_DEFAULTS,
                    ),
                ])
            if range_file is not None:
                solver_cmd.extend(["--range-path", str(range_file)])
            else:
                solver_cmd.extend(["--oop-range", current_oop or "", "--ip-range", current_ip or ""])
            if args.stall_timeout is not None:
                solver_cmd.extend(["--stall-timeout", str(args.stall_timeout)])
            if args.no_output_timeout is not None:
                solver_cmd.extend(["--no-output-timeout", str(args.no_output_timeout)])
            if args.estimate_memory:
                solver_cmd.append("--estimate-memory")
            batch_success = _run(solver_cmd)
            batch_report = _read_batch_report(report_path, batch)
            if batch_report:
                batch_completed = batch_report["completed_indices"]
                batch_failed = batch_report["failed_indices"]
                batch_skipped = batch_report["skipped_indices"]
                batch_abnormal = batch_report["abnormal_indices"]
            elif batch_success:
                batch_completed = batch
                batch_failed = []
                batch_skipped = []
                batch_abnormal = []
            else:
                batch_completed = []
                batch_failed = batch
                batch_skipped = []
                batch_abnormal = batch

            _extend_unique(completed_indices, batch_completed)
            _extend_unique(failed_indices, batch_failed)
            _extend_unique(skipped_indices, batch_skipped)

            last_solver_success = batch_success and len(batch_failed) == 0
            if batch_failed:
                print(
                    f"[Failed] Solver batch {i} has {len(batch_failed)} failed board(s) "
                    f"({len(batch_skipped)} skipped, {len(batch_abnormal)} abnormal); continue to next batch"
                )
            if tracker:
                tracker.update(
                    completed_indices=completed_indices,
                    failed_indices=failed_indices,
                    skipped_indices=skipped_indices,
                    completed_count=len(completed_indices),
                    failed_count=len(failed_indices),
                    skipped_count=len(skipped_indices),
                    last_solver_success=last_solver_success,
                    last_batch_report=str(report_path),
                    last_batch_completed_indices=batch_completed,
                    last_batch_failed_indices=batch_failed,
                    last_batch_skipped_indices=batch_skipped,
                    last_batch_abnormal_indices=batch_abnormal,
                )

            export_count = _count_export_files(args.export_format)
            if export_count >= batch_size:
                json_count = _count_json()
                parquet_count = _count_parquet()
                print(
                    f"\n[Upload] {export_count} files in {RESULTS_DIR} "
                    f"(JSON={json_count}, Parquet={parquet_count}, threshold={batch_size})"
                )
                if tracker:
                    tracker.update(
                        phase="uploading",
                        pending_export_count=export_count,
                    )
                if do_upload:
                    ok, upload_code = _process_artifacts(
                        True,
                        repo_id=repo_id,
                        upload_format=args.upload_format,
                        upload_attempt_timeout=args.upload_attempt_timeout,
                    )
                    if ok:
                        print("[Upload] Success")
                        if tracker:
                            tracker.update(phase="solving", last_upload_success=True)
                    else:
                        upload_failures += 1
                        do_upload = False
                        upload_disabled_due_network = True
                        summary.set_upload_state(
                            upload_enabled=False,
                            upload_failures=upload_failures,
                            upload_disabled_due_network=True,
                        )
                        print("检测到当前网络环境可能存在问题，关闭上传，仅求解。")
                        print(f"[Upload] Files kept in {RESULTS_DIR}; will retry one final upload after all solving is done")
                        if tracker:
                            tracker.update(
                                phase="solving",
                                upload_enabled=False,
                                upload_disabled_due_network=True,
                                last_upload_success=False,
                                last_upload_exit_code=upload_code,
                            )
                else:
                    print(f"[Upload] 当前上传已关闭，仅求解；文件保留在 {RESULTS_DIR}，等待最终统一上传")
                    if tracker:
                        tracker.update(phase="solving")

        remaining = _count_pending_result_files(args.export_format, args.upload_format)
        if remaining > 0:
            json_count = _count_json()
            parquet_count = _count_parquet()
            print(f"\n[Cleanup] {remaining} files remaining in {RESULTS_DIR} "
                  f"(JSON={json_count}, Parquet={parquet_count})")
            if tracker:
                tracker.update(phase="cleanup", pending_export_count=remaining)
            final_upload = not args.no_upload
            if upload_disabled_due_network and final_upload:
                print("[Final Upload] 全部求解完成，正在进行最后一次统一上传尝试")
            ok, upload_code = _process_artifacts(
                final_upload,
                repo_id=repo_id,
                upload_format=args.upload_format,
                upload_attempt_timeout=args.upload_attempt_timeout,
            )
            if ok and final_upload:
                print("[Final Upload] 最终统一上传成功")
                upload_failures = 0
                upload_disabled_due_network = False
            elif ok:
                print("[Cleanup] Processed local artifacts; upload disabled")
            else:
                upload_failures += 1
                remaining_files = _count_pending_result_files(args.export_format, args.upload_format)
                print(f"[Final Upload] 最终统一上传失败 (exit code: {upload_code})")
                print(
                    f"本地 {RESULTS_DIR} 下有 {remaining_files} 个求解结果，请检查当前网络环境后，"
                    f"尝试使用命令 {_manual_upload_command(repo_id, args.upload_format)} 进行手动上传"
                )

        summary.set_upload_state(
            upload_enabled=do_upload,
            upload_failures=upload_failures,
            upload_disabled_due_network=upload_disabled_due_network,
        )
        if upload_failures > 0 or upload_disabled_due_network:
            remaining_files = _count_pending_result_files(args.export_format, args.upload_format)
            print(f"[Completed] Pipeline finished with {upload_failures} upload failure(s)")
            if remaining_files > 0:
                print(
                    f"  本地 {RESULTS_DIR} 下有 {remaining_files} 个求解结果，请检查当前网络环境后，"
                    f"尝试使用命令 {_manual_upload_command(repo_id, args.upload_format)} 进行手动上传"
                )
            _finish_pipeline(
                "completed_with_upload_failures",
                "正常结束（存在上传失败）",
                upload_failures=upload_failures,
                remaining_export_files=remaining_files,
            )
        else:
            print("[Completed] Pipeline execution completed")
            _finish_pipeline("completed", "正常结束", upload_failures=0)
    except KeyboardInterrupt:
        summary.set_upload_state(
            upload_enabled=do_upload,
            upload_failures=upload_failures,
            upload_disabled_due_network=upload_disabled_due_network,
        )
        _finish_pipeline("interrupted", "用户中断 (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        summary.set_upload_state(
            upload_enabled=do_upload,
            upload_failures=upload_failures,
            upload_disabled_due_network=upload_disabled_due_network,
        )
        _finish_pipeline("failed", f"异常退出: {e}", error=str(e))
        raise
    finally:
        summary.set_upload_state(
            upload_enabled=do_upload,
            upload_failures=upload_failures,
            upload_disabled_due_network=upload_disabled_due_network,
        )
        if not summary._printed:
            _finish_pipeline("exited", "进程退出")


if __name__ == "__main__":
    main()
