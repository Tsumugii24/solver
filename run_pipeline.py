#!/usr/bin/env python3
"""
自动化流水线：Solver → JSON/Parquet → Hugging Face

每个 batch 求解完成后，检查 results/ 目录下文件数量。达到 batch_size 阈值时
尝试上传（最多重试 5 次）。若上传失败，文件留在 results/ 目录，和后续 batch
的结果一起累积，在下一次达到阈值时再次尝试上传。

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

  dataset 名（repo_id 最后一段）应对应 ranges 下的 range 文件名（不含路径）：
  例如 dataset 为 sia-12-sod-30 则匹配 ranges/sia-sod/sia-12-sod-30.txt，
  soa-50-sid-30 则匹配 ranges/soa-sid/soa-50-sid-30.txt，
  3ia-16.5-3od-13 则匹配 ranges/3ia-3od/3ia-16.5-3od-13.txt，
  sia-16-sod-21.5-open2.5 则匹配 ranges/sia-sod-open2.5/ 或文件名含 open2.5；
  根目录遗留的 sia-100bb.txt 仍可匹配。

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
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
RESULTS_DIR = SCRIPT_DIR / "results"
UPLOAD_DIR = SCRIPT_DIR / "upload"
RANGES_DIR = SCRIPT_DIR / "ranges"
SUPPORTED_EXPORT_FORMATS = ["json"] if sys.platform == "win32" else ["json", "parquet", "parquet_native"]
SUPPORTED_UPLOAD_FORMATS = ["json", "parquet"]
DEFAULT_EXPORT_FORMAT = "json" if sys.platform == "win32" else "parquet"
DEFAULT_UPLOAD_FORMAT = "parquet"
def _default_pipeline_status_file() -> Path:
    """固定默认路径（用户家目录下），与 solver 当前工作目录无关，且无需 root 权限。"""
    return Path.home() / "run" / "solver_running_status.json"


DEFAULT_PIPELINE_STATUS_FILE = _default_pipeline_status_file()

from auto_run_solver import (
    SCENARIO_SUBDIRS,
    infer_scenario_from_name,
    infer_scenario_from_range_path,
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


def _run(cmd: list, cwd: Path = None) -> bool:
    """执行命令，返回是否成功"""
    cwd = cwd or SCRIPT_DIR
    r = subprocess.run(cmd, cwd=str(cwd))
    return r.returncode == 0


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
) -> bool:
    json_count = _count_json_in_dir(target_dir)
    ok = True

    if upload_format == "parquet" and json_count > 0:
        if not _ensure_pyarrow():
            return False
        ok = _run([sys.executable, str(UPLOAD_DIR / "batch_json_to_parquet.py"), str(target_dir)])
        if not ok:
            return False

    ready_count = _count_upload_files_in_dir(target_dir, upload_format)
    if ready_count == 0:
        return ok

    if ok and upload:
        if not repo_id:
            print("[Error] Missing Hugging Face repo_id")
            return False
        ok = _run([
            sys.executable,
            str(UPLOAD_DIR / "upload_to_hf.py"),
            str(target_dir),
            "--repo-id",
            repo_id,
            "--file-format",
            upload_format,
        ])
        if ok:
            deleted = _delete_json_in_dir(target_dir) if upload_format == "json" else _delete_parquets_in_dir(target_dir)
            if deleted > 0:
                print(f"[Cleanup] Deleted {deleted} uploaded {upload_format} files")
    return ok


def _process_artifacts(
    upload: bool,
    repo_id: Optional[str] = None,
    upload_format: str = DEFAULT_UPLOAD_FORMAT,
) -> bool:
    return _process_artifacts_in_dir(RESULTS_DIR, upload, repo_id=repo_id, upload_format=upload_format)


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
    """支持从 URL 或 owner/repo 形式解析标准 repo_id。"""
    from check_missing import parse_repo_id

    return parse_repo_id(repo_id)


def _filter_requested_indices_by_hf(
    repo_id: str,
    requested_indices: list[int],
    cards_file: str,
) -> tuple[list[int], int, int, int]:
    """
    根据 Hugging Face dataset 已存在的牌面过滤待求解序号。

    Returns:
        (remaining_indices, total_repo_existing, skipped_in_requested, total_boards)
    """
    from check_missing import board_to_filename, list_boards_in_hf_dataset

    all_boards = _read_all_boards(cards_file)
    boards_in_hf = list_boards_in_hf_dataset(repo_id)

    requested_set = set(requested_indices)
    remaining_indices: list[int] = []
    total_repo_existing = 0
    skipped_in_requested = 0

    for idx, board in enumerate(all_boards, start=1):
        exists_in_repo = board_to_filename(board) in boards_in_hf
        if exists_in_repo:
            total_repo_existing += 1
        if idx in requested_set:
            if exists_in_repo:
                skipped_in_requested += 1
            else:
                remaining_indices.append(idx)

    return remaining_indices, total_repo_existing, skipped_in_requested, len(all_boards)



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
        choices=list(SCENARIO_SUBDIRS),
        help="未提供 --repo-id 时与 --range-file 联用：ranges/<scenario>/ 下的场景子目录",
    )
    parser.add_argument(
        "--range-file",
        type=str,
        default=None,
        help="未提供 --repo-id 时：场景目录内文件名，如 sia-12-sod-30.txt（与 --scenario 搭配）",
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

    if not args.no_upload and not args.convert_only:
        if args.upload_format == "json" and args.export_format != "json":
            print("[Error] --upload-format json requires --export-format json")
            sys.exit(1)

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
    print(f"Total Tasks: {len(indices)} boards")
    print(f"Solver Batches: {len(batches)}, max {batch_size} per batch")
    print(f"Export Format: {args.export_format}")
    print(f"Upload Format: {args.upload_format}")
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

    if repo_id:
        try:
            repo_id = _normalize_repo_id(repo_id)
        except Exception as e:
            print(f"[Error] Invalid repo_id: {e}")
            sys.exit(1)
        range_file = _find_range_file(repo_id)
        if not range_file:
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

        # 加载并显示 range 信息
        try:
            oop_range, ip_range = _load_ranges_from_file(range_file)
            print(f"[Range] File: {range_file.name}")
            print(f"  OOP: {oop_range[:80]}{'...' if len(oop_range) > 80 else ''}")
            print(f"  IP:  {ip_range[:80]}{'...' if len(ip_range) > 80 else ''}")
        except Exception as e:
            print(f"[错误] 读取 range 文件失败: {e}")
            sys.exit(1)

        print(f"[HF] Target Repository: https://huggingface.co/datasets/{repo_id}")
        if not _ensure_hf_logged_in():
            print("[Error] Unable to upload: Please login to HF or set HF_TOKEN")
            sys.exit(1)

    # --no-upload 且未提供 --repo-id：用 --scenario + --range-file 指向 ranges/<scenario>/
    if range_file is None and args.scenario and args.range_file:
        cand = RANGES_DIR / args.scenario / args.range_file
        if cand.is_file():
            range_file = cand.resolve()
        else:
            alt = RANGES_DIR / args.range_file
            if alt.is_file():
                range_file = alt.resolve()
            else:
                print(f"[错误] 找不到 range 文件: {cand}")
                print(f"  亦未在 ranges/ 根目录找到: {alt}")
                print(f"  ranges/ 下可用: {_list_range_txt_for_errors()}")
                sys.exit(1)

    if not args.convert_only:
        if range_file is None or not range_file.is_file():
            print("[错误] 运行 solver 需要 range：请使用 --repo-id（HF dataset 名应对应 ranges 下某 .txt 基名），")
            print("  或同时指定 --scenario 与 --range-file（常用于仅本地求解、不上传）。")
            print(f"  ranges/ 下可用: {_list_range_txt_for_errors()}")
            sys.exit(1)

    if repo_id and not args.convert_only:
        try:
            requested_count = len(indices)
            indices, repo_existing_count, skipped_in_requested, total_boards = _filter_requested_indices_by_hf(
                repo_id=repo_id,
                requested_indices=indices,
                cards_file=args.file,
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
        if not indices:
            print("[Info] Requested range is already complete in the target dataset")

    do_upload = not args.no_upload
    upload_failures = 0
    tracker: Optional[PipelineStatusTracker] = None
    if not args.no_status_file:
        status_file = _resolve_status_file(args.status_file)
        tracker = PipelineStatusTracker(status_file)
        scenario = infer_scenario_from_range_path(range_file) if range_file else None
        try:
            rel_range = str(range_file.resolve().relative_to(SCRIPT_DIR)) if range_file else None
        except ValueError:
            rel_range = range_file.name if range_file else None
        tracker.update(
            status="running",
            repo_id=repo_id,
            dataset_name=_dataset_name_from_repo_id(repo_id) if repo_id else None,
            scenario=scenario,
            range_file=rel_range,
            upload_enabled=do_upload,
            convert_only=args.convert_only,
            no_upload=args.no_upload,
            total_tasks=len(indices),
            total_batches=len(batches),
            current_batch=0,
            export_format=args.export_format,
            upload_format=args.upload_format,
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
                if tracker:
                    tracker.finalize("completed", message="no artifacts to process")
                sys.exit(0)
            _process_artifacts(do_upload, repo_id=repo_id, upload_format=args.upload_format)
            print("\n[Completed]")
            if tracker:
                tracker.finalize("completed", mode="convert_only")
            sys.exit(0)

        for i, batch in enumerate(batches, 1):
            expr = _compress_indices(batch)
            if tracker:
                tracker.update(
                    current_batch=i,
                    batch_expr=expr,
                    batch_size_current=len(batch),
                )
            print(f"\n{'='*60}")
            print(f"[Batch {i}/{len(batches)}] Solving: {expr}")
            print("=" * 60)

            solver_cmd = [
                sys.executable, str(SCRIPT_DIR / "auto_run_solver.py"),
                expr,
                "--file", args.file,
                "--scenario", infer_scenario_from_range_path(range_file),
                "--range-file", range_file.name,
                "--thread-num", str(args.thread_num),
                "--use-isomorphism", str(args.use_isomorphism),
                "--max-iteration", str(args.max_iteration),
                "--dump-format", args.export_format,
            ]
            if args.stall_timeout is not None:
                solver_cmd.extend(["--stall-timeout", str(args.stall_timeout)])
            if args.no_output_timeout is not None:
                solver_cmd.extend(["--no-output-timeout", str(args.no_output_timeout)])
            if args.estimate_memory:
                solver_cmd.append("--estimate-memory")
            if not _run(solver_cmd):
                print(f"[Failed] Solver batch {i} not fully successful, continue to next batch")
                if tracker:
                    tracker.update(last_solver_success=False)

            export_count = _count_export_files(args.export_format)
            if export_count >= batch_size:
                json_count = _count_json()
                parquet_count = _count_parquet()
                print(
                    f"\n[Upload] {export_count} files in results/ "
                    f"(JSON={json_count}, Parquet={parquet_count}, threshold={batch_size})"
                )
                if tracker:
                    tracker.update(
                        phase="uploading",
                        pending_export_count=export_count,
                    )
                ok = _process_artifacts(do_upload, repo_id=repo_id, upload_format=args.upload_format)
                if ok:
                    print("[Upload] Success")
                    if tracker:
                        tracker.update(phase="solving", last_upload_success=True)
                else:
                    upload_failures += 1
                    print("[Upload] Failed after retries, files kept in results/ for next attempt")
                    if tracker:
                        tracker.update(phase="solving", last_upload_success=False)

        remaining = _count_export_files(args.export_format)
        if remaining > 0:
            json_count = _count_json()
            parquet_count = _count_parquet()
            print(f"\n[Cleanup] {remaining} files remaining in results/ "
                  f"(JSON={json_count}, Parquet={parquet_count})")
            if tracker:
                tracker.update(phase="cleanup", pending_export_count=remaining)
            ok = _process_artifacts(do_upload, repo_id=repo_id, upload_format=args.upload_format)
            if ok:
                print("[Cleanup] Upload success")
            else:
                upload_failures += 1
                print("[Cleanup] Upload failed, files kept in results/")

        print("\n" + "=" * 60)
        if upload_failures > 0:
            remaining_files = _count_export_files(args.export_format)
            print(f"[Completed] Pipeline finished with {upload_failures} upload failure(s)")
            if remaining_files > 0:
                print(f"  {remaining_files} files remain in results/ — "
                      f"re-run with --convert-only to retry upload")
            if tracker:
                tracker.finalize(
                    "completed_with_upload_failures",
                    upload_failures=upload_failures,
                    remaining_export_files=remaining_files,
                )
        else:
            print("[Completed] Pipeline execution completed")
            if tracker:
                tracker.finalize("completed", upload_failures=0)
        print("=" * 60)
    except Exception as e:
        if tracker:
            tracker.finalize("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
