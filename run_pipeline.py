#!/usr/bin/env python3
"""
自动化流水线：Solver → JSON/Parquet → Hugging Face

转换/上传触发条件：结果目录下积累已解算的牌面数量 >= batch_size 时，
会将当前产物切到后台暂存目录继续转换/上传，前台解算不等待上传完成。

用法:
  python run_pipeline.py                         # 默认求解全部牌面，满足阈值后后台转换+上传
  python run_pipeline.py 1-20                    # 求解 1-20，满足阈值后后台转换+上传
  python run_pipeline.py 1-20 --batch-size 5    # 同上
  python run_pipeline.py 1,5,10,15,20            # 指定序号
  python run_pipeline.py 1-20 --no-upload       # 只求解+转换，不上传
  python run_pipeline.py 1-20 --convert-only     # 仅转换已有 JSON 并上传（不跑 solver）

环境变量:
  HF_TOKEN 或 HUGGINGFACE_HUB_TOKEN: 未登录时自动用此 token 登录
  HF_REPO_ID: 目标 Hugging Face dataset repo_id；不传则启动时交互输入
"""

import argparse
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Optional

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
RESULTS_DIR = SCRIPT_DIR / "results"
UPLOAD_DIR = SCRIPT_DIR / "upload"
UPLOAD_STAGING_DIR = SCRIPT_DIR / "_upload_staging"
SUPPORTED_DUMP_FORMATS = ["json", "parquet"] if sys.platform == "win32" else ["json", "parquet", "parquet_native"]


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


def _do_convert_and_upload_in_dir(target_dir: Path, upload: bool, repo_id: Optional[str] = None) -> bool:
    """处理指定目录中的 JSON/Parquet 产物，可选上传。返回是否成功。"""
    json_count = _count_json_in_dir(target_dir)
    parquet_count = _count_parquet_in_dir(target_dir)
    ok = True

    if json_count > 0:
        if not _ensure_pyarrow():
            return False
        ok = _run([sys.executable, str(UPLOAD_DIR / "batch_json_to_parquet.py"), str(target_dir)])
        if not ok:
            return False
        parquet_count = _count_parquet_in_dir(target_dir)

    if parquet_count == 0:
        return ok

    if ok and upload:
        if not repo_id:
            print("[错误] 缺少 Hugging Face repo_id，无法上传")
            return False
        ok = _run([
            sys.executable,
            str(UPLOAD_DIR / "upload_to_hf.py"),
            str(target_dir),
            "--repo-id",
            repo_id,
        ])
        if ok:
            n = _delete_parquets_in_dir(target_dir)
            if n > 0:
                print(f"[清理] 已删除 {n} 个已上传的 parquet 文件")
    return ok


def _do_convert_and_upload(upload: bool, repo_id: Optional[str] = None) -> bool:
    """处理 results 中的 JSON/Parquet 产物，可选上传。返回是否成功。"""
    return _do_convert_and_upload_in_dir(RESULTS_DIR, upload, repo_id=repo_id)


def _prompt_repo_id() -> str:
    """交互式获取要上传的 HF dataset repo_id。"""
    env_repo_id = os.environ.get("HF_REPO_ID")
    if env_repo_id:
        print(f"[HF] 检测到 HF_REPO_ID={env_repo_id}")
        return env_repo_id.strip()

    print("[提示] 请输入要上传的 Hugging Face dataset repo_id")
    print("  例如: username/dataset-name （或按 Enter 退出）")
    repo_id = input("  repo_id: ").strip()
    if not repo_id:
        print("[错误] 未提供 repo_id，退出")
        sys.exit(1)
    return repo_id


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


@dataclass
class UploadJob:
    """后台上传任务。"""
    job_id: int
    staging_dir: Path
    json_count: int
    parquet_count: int
    trigger: str


class AsyncUploadManager:
    """把 results 中的产物切到暂存区并在后台顺序上传。"""

    def __init__(self, enabled: bool, repo_id: Optional[str] = None):
        self.enabled = enabled
        self.repo_id = repo_id
        self._queue: Queue[Optional[UploadJob]] = Queue()
        self._worker: Optional[threading.Thread] = None
        self._started = False
        self._job_counter = 0
        self.failures: list[UploadJob] = []

    def start(self) -> None:
        if not self.enabled or self._started:
            return
        UPLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="hf-upload-worker",
            daemon=True,
        )
        self._worker.start()
        self._started = True

    def submit_results(self, trigger: str) -> bool:
        """将当前 results 中的产物移动到暂存目录，并提交后台上传。"""
        if not self.enabled:
            return False

        json_files = sorted(RESULTS_DIR.glob("*.json"))
        parquet_files = sorted(RESULTS_DIR.glob("*.parquet"))
        if not json_files and not parquet_files:
            return False

        self._job_counter += 1
        job_id = self._job_counter
        staging_dir = UPLOAD_STAGING_DIR / f"job_{job_id:04d}"
        staging_dir.mkdir(parents=True, exist_ok=False)

        for src in json_files + parquet_files:
            shutil.move(str(src), str(staging_dir / src.name))

        job = UploadJob(
            job_id=job_id,
            staging_dir=staging_dir,
            json_count=len(json_files),
            parquet_count=len(parquet_files),
            trigger=trigger,
        )
        self.start()
        self._queue.put(job)
        print(
            f"[后台上传] 已切出任务 #{job.job_id}: "
            f"JSON={job.json_count}, Parquet={job.parquet_count} -> {job.staging_dir.name}"
        )
        return True

    def wait(self) -> bool:
        """等待所有后台上传完成，返回是否全部成功。"""
        if not self.enabled or not self._started:
            return True

        self._queue.join()
        self._queue.put(None)
        if self._worker is not None:
            self._worker.join()

        try:
            if UPLOAD_STAGING_DIR.exists() and not any(UPLOAD_STAGING_DIR.iterdir()):
                UPLOAD_STAGING_DIR.rmdir()
        except OSError:
            pass
        return not self.failures

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return

                print(
                    f"\n[后台上传] 开始任务 #{job.job_id} "
                    f"(触发: {job.trigger}, JSON={job.json_count}, Parquet={job.parquet_count})"
                )
                ok = _do_convert_and_upload_in_dir(job.staging_dir, upload=True, repo_id=self.repo_id)
                if ok:
                    try:
                        if job.staging_dir.exists() and not any(job.staging_dir.iterdir()):
                            job.staging_dir.rmdir()
                    except OSError:
                        pass
                    print(f"[后台上传] 任务 #{job.job_id} 完成")
                else:
                    self.failures.append(job)
                    print(f"[后台上传] 任务 #{job.job_id} 失败，文件保留在: {job.staging_dir}")
            except Exception as e:
                if job is not None:
                    self.failures.append(job)
                    print(f"[后台上传] 任务 #{job.job_id} 异常: {e}")
            finally:
                self._queue.task_done()


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
                        help="results 目录下 JSON 积累到此数时触发转换+上传（默认 5）")
    parser.add_argument("--no-upload", action="store_true",
                        help="只求解+转换，不上传到 HF")
    parser.add_argument("--convert-only", action="store_true",
                        help="仅转换已有 JSON 并上传，不跑 solver")
    parser.add_argument("--repo-id", type=str, default=None,
                        help="目标 Hugging Face dataset repo_id；不传则启动时交互输入")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅预览，不执行")
    # 透传给 auto_run_solver
    parser.add_argument("--file", type=str, default="cards.txt")
    parser.add_argument("--thread-num", type=int, default=-1)
    parser.add_argument("--use-isomorphism", type=int, choices=[0, 1], default=1)
    parser.add_argument("--max-iteration", type=int, default=300)
    parser.add_argument("--stall-timeout", type=int, default=180)
    parser.add_argument(
        "--dump-format",
        type=str,
        default="parquet",
        choices=SUPPORTED_DUMP_FORMATS,
        help="透传给 auto_run_solver 的导出格式（默认: parquet）"
    )
    args = parser.parse_args()

    try:
        total = len(_read_all_boards(args.file))
    except Exception as e:
        print(f"[Error] Unable to read cards file: {e}")
        sys.exit(1)
    if args.range.lower() == "all":
        indices = list(range(1, total + 1))
    else:
        indices = _parse_range(args.range, total)
    if not indices:
        print("[错误] 没有有效的序号")
        sys.exit(1)

    batch_size = max(1, args.batch_size)
    batches = [indices[i:i + batch_size] for i in range(0, len(indices), batch_size)]
    last_batch_size = len(batches[-1]) if batches else 0
    has_remainder = last_batch_size > 0 and last_batch_size < batch_size and len(batches) > 1

    print("=" * 60)
    print("Automatic Pipeline: Solver → Parquet Results → Upload to HuggingFace")
    print("=" * 60)
    print(f"Total Tasks: {len(indices)} boards")
    print(f"Solver Batches: {len(batches)}, max {batch_size} per batch")
    print(f"Trigger Condition: When results count >= {batch_size}, move to background for processing and uploading")
    if has_remainder:
        print(f"  (Last batch has {last_batch_size} boards; any remaining JSON will be converted at the end)")
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
    if repo_id:
        try:
            repo_id = _normalize_repo_id(repo_id)
        except Exception as e:
            print(f"[Error] Invalid repo_id: {e}")
            sys.exit(1)

    # 需要上传时，先确认 repo_id 并检查 HF 登录
    if not args.no_upload:
        if not repo_id:
            repo_id = _prompt_repo_id()
        try:
            repo_id = _normalize_repo_id(repo_id)
        except Exception as e:
            print(f"[Error] Invalid repo_id: {e}")
            sys.exit(1)
        print(f"[HF] Target Repository: https://huggingface.co/datasets/{repo_id}")
        if not _ensure_hf_logged_in():
            print("[Error] Unable to upload: Please login to HF or set HF_TOKEN")
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

    upload_manager = AsyncUploadManager(enabled=not args.no_upload, repo_id=repo_id)

    if args.convert_only:
        print("\n[Mode] Only process existing results and upload (no solver)")
        if _count_json() == 0 and _count_parquet() == 0:
            print("[提示] results directory has no JSON or Parquet files")
            sys.exit(0)
        _do_convert_and_upload(not args.no_upload, repo_id=repo_id)
        print("\n[Completed]")
        sys.exit(0)

    for i, batch in enumerate(batches, 1):
        expr = _compress_indices(batch)
        print(f"\n{'='*60}")
        print(f"[Batch {i}/{len(batches)}] Solving: {expr}")
        print("=" * 60)

        # 1. Run solver
        solver_cmd = [
            sys.executable, str(SCRIPT_DIR / "auto_run_solver.py"),
            expr,
            "--file", args.file,
            "--thread-num", str(args.thread_num),
            "--use-isomorphism", str(args.use_isomorphism),
            "--max-iteration", str(args.max_iteration),
            "--stall-timeout", str(args.stall_timeout),
            "--dump-format", args.dump_format,
        ]
        if not _run(solver_cmd):
            print(f"[Failed] Solver batch {i} not fully successful, continue to next batch")

        # 2. 检查 results 下积累的 JSON/Parquet 数量，达到阈值则处理/上传
        json_count = _count_json()
        parquet_count = _count_parquet()
        if json_count >= batch_size or parquet_count >= batch_size:
            print(f"\n[Trigger] results JSON={json_count}, Parquet={parquet_count} (threshold {batch_size})")
            if json_count > 0:
                print("[Convert] JSON → Parquet")
            else:
                print("[Process] Directly upload Parquet files")
            if args.no_upload:
                _do_convert_and_upload(False, repo_id=repo_id)
            else:
                if upload_manager.submit_results(f"Threshold {batch_size}"):
                    print("[Submitted] Background task submitted, continue to next batch")

    # 3. 结束时处理剩余 JSON/Parquet（无法整除或部分失败导致的数量不足）
    json_count = _count_json()
    parquet_count = _count_parquet()
    if json_count > 0 or parquet_count > 0:
        print(f"\n[Cleanup] results JSON={json_count}, Parquet={parquet_count}")
        if args.no_upload:
            _do_convert_and_upload(False, repo_id=repo_id)
        else:
            if upload_manager.submit_results("Cleanup"):
                print("[Submitted] Cleanup task submitted, waiting for background upload to complete")

    if not args.no_upload:
        if not upload_manager.wait():
            print("[Error] Background upload has failed tasks, please check _upload_staging directory")
            sys.exit(1)

    print("\n" + "=" * 60)
    print("[Completed] Pipeline execution completed")
    print("=" * 60)


if __name__ == "__main__":
    main()
