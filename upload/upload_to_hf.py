#!/usr/bin/env python3
"""
将 Parquet 文件上传到 Hugging Face Dataset，使用 Xet 管理大文件。

目标: https://huggingface.co/datasets/{repo_id}

用法:
  python upload_to_hf.py [dir] --repo-id <user_or_org/dataset_name>
  python upload_to_hf.py --dry-run [dir] --repo-id <user_or_org/dataset_name>

前置:
  pip install -U huggingface_hub
  huggingface-cli login
"""

import argparse
import multiprocessing
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 2
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 120
FILE_PATTERNS = {
    "json": "*.json",
    "parquet": "*.parquet",
}

# Xet 高性能模式：充分利用带宽和 CPU
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

try:
    from huggingface_hub import HfApi
except ImportError:
    print("pip install -U huggingface_hub")
    sys.exit(1)


def dataset_url(repo_id: str) -> str:
    return f"https://huggingface.co/datasets/{repo_id}"


def resolve_dataset_repo_id(api: HfApi, repo_id: str) -> tuple[str, str | None]:
    """Resolve repo_id to namespace/name. Bare names use default namespace."""
    from check_missing import default_hf_namespace, parse_repo_id

    repo_id_input = repo_id.strip()
    full_repo_id = parse_repo_id(repo_id_input)
    if "/" not in repo_id_input and "huggingface.co" not in repo_id_input.casefold():
        return full_repo_id, default_hf_namespace()
    return full_repo_id, None


def print_upload_plan(
    root: Path,
    file_count: int,
    file_format: str,
    repo_id_input: str,
    repo_id: str,
    auto_namespace: str | None,
) -> None:
    print(f"Found {file_count} {file_format} files in {root}")
    if auto_namespace:
        print(
            f"Repo id: {repo_id_input} -> {repo_id} "
            f"(namespace: {auto_namespace})"
        )
    print(f"Target: {dataset_url(repo_id)}")
    print(f"HF_XET_HIGH_PERFORMANCE={os.environ.get('HF_XET_HIGH_PERFORMANCE', 'not set')}")


def _upload_once(root: str, repo_id: str, pattern: str) -> None:
    api = HfApi()
    api.upload_large_folder(
        folder_path=root,
        repo_id=repo_id,
        repo_type="dataset",
        allow_patterns=pattern,
    )


def delete_uploaded_files(root: Path, file_format: str) -> int:
    """删除已上传成功的本地文件，逻辑与 run_pipeline 一致。"""
    glob_pattern = FILE_PATTERNS[file_format]
    deleted = 0
    for f in root.glob(glob_pattern):
        try:
            f.unlink()
            deleted += 1
        except OSError as e:
            print(f"[警告] 删除 {f.name} 失败: {e}")
    return deleted


def upload_with_timeout(root: Path, repo_id: str, pattern: str, timeout_seconds: int) -> None:
    process = multiprocessing.Process(
        target=_upload_once,
        args=(str(root), repo_id, pattern),
    )
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join()
        raise TimeoutError(f"upload attempt timed out after {timeout_seconds}s")

    if process.exitcode != 0:
        raise RuntimeError(f"upload process exited with code {process.exitcode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload result files to HF with Xet")
    parser.add_argument("dir", nargs="?", default="results", help="Directory with result files")
    parser.add_argument("--repo-id", required=True, help="Target HF dataset repo_id, e.g. user/dataset")
    parser.add_argument(
        "--file-format",
        choices=sorted(FILE_PATTERNS),
        default="parquet",
        help="File format to upload (default: parquet)",
    )
    parser.add_argument(
        "--attempt-timeout",
        type=int,
        default=int(os.environ.get("HF_UPLOAD_ATTEMPT_TIMEOUT", DEFAULT_ATTEMPT_TIMEOUT_SECONDS)),
        help="Seconds before one upload attempt is treated as stuck (default: 120)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        help="Maximum upload attempts before giving up (default: 5)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no upload")
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}")
        sys.exit(1)

    pattern = FILE_PATTERNS[args.file_format]
    files = list(root.glob(pattern))
    if not files:
        print(f"No {args.file_format} files found")
        sys.exit(0)

    api = HfApi()
    repo_id, auto_namespace = resolve_dataset_repo_id(api, args.repo_id)
    print_upload_plan(root, len(files), args.file_format, args.repo_id, repo_id, auto_namespace)
    print(f"Attempt timeout: {args.attempt_timeout}s")
    if args.dry_run:
        print("\nDRY RUN - no upload")
        sys.exit(0)

    max_retries = max(1, args.max_retries)
    for attempt in range(1, max_retries + 1):
        try:
            print(f"\nAttempt {attempt}/{max_retries}")
            upload_with_timeout(root, repo_id, pattern, args.attempt_timeout)
            print(f"Done: {dataset_url(repo_id)}")
            deleted = delete_uploaded_files(root, args.file_format)
            if deleted > 0:
                print(f"[Cleanup] Deleted {deleted} uploaded {args.file_format} files")
            sys.exit(0)
        except KeyboardInterrupt:
            print("\nUpload interrupted by user")
            sys.exit(1)
        except Exception as e:
            exit_code = 124 if isinstance(e, TimeoutError) else 1
            if attempt >= max_retries:
                print(f"\nMax retries ({max_retries}) reached. Last error: {e}")
                sys.exit(exit_code)
            delay = INITIAL_RETRY_DELAY * (2 ** (attempt - 1))
            print(f"Error (attempt {attempt}): {e}")
            print(f"Retrying in {delay}s...")
            time.sleep(delay)


if __name__ == "__main__":
    main()
