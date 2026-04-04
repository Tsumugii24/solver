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
import os
import sys
import time
from pathlib import Path

MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 2
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

    print(f"Found {len(files)} {args.file_format} files in {root}")
    print(f"Target: https://huggingface.co/datasets/{args.repo_id}")
    print(f"HF_XET_HIGH_PERFORMANCE={os.environ.get('HF_XET_HIGH_PERFORMANCE', 'not set')}")
    if args.dry_run:
        print("\nDRY RUN - no upload")
        sys.exit(0)

    api = HfApi()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"\nAttempt {attempt}/{MAX_RETRIES}")
            api.upload_large_folder(
                folder_path=str(root),
                repo_id=args.repo_id,
                repo_type="dataset",
                allow_patterns=pattern,
            )
            print("Done")
            sys.exit(0)
        except KeyboardInterrupt:
            print("\nUpload interrupted by user")
            sys.exit(1)
        except Exception as e:
            if attempt >= MAX_RETRIES:
                print(f"\nMax retries ({MAX_RETRIES}) reached. Last error: {e}")
                sys.exit(1)
            delay = INITIAL_RETRY_DELAY * (2 ** (attempt - 1))
            print(f"Error (attempt {attempt}): {e}")
            print(f"Retrying in {delay}s...")
            time.sleep(delay)


if __name__ == "__main__":
    main()
