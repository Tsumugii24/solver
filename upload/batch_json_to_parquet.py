#!/usr/bin/env python3
"""
批量将 JSON 转为 Parquet，转一个删一个以节省空间。
最终得到与 JSON 同名的 .parquet 文件。

用法:
  python batch_json_to_parquet.py [dir]
  python batch_json_to_parquet.py --dry-run [dir]   # 仅预览，不执行
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    print("pip install pyarrow")
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable  # pip install tqdm for progress bar


def _make_row(data: dict) -> dict:
    return {"data": json.dumps(data, ensure_ascii=False)}


def convert_and_delete(json_path: Path, dry_run: bool = False) -> bool:
    """Convert JSON to Parquet, then delete JSON. Returns True on success."""
    parquet_path = json_path.with_suffix(".parquet")
    if parquet_path.exists():
        if not dry_run and json_path.exists():
            json_path.unlink()
        return True

    if dry_run:
        return True

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        table = pa.Table.from_pylist([_make_row(data)])
        pq.write_table(table, parquet_path)
        json_path.unlink(missing_ok=True)
        return True
    except Exception as e:
        print(f"  ERROR {json_path.name}: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch JSON -> Parquet, delete JSON after each")
    parser.add_argument("dir", nargs="?", default=".", help="Directory with JSON files")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not convert or delete")
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}")
        sys.exit(1)

    files = sorted(root.glob("*.json"))
    if not files:
        print("No JSON files found")
        sys.exit(0)

    print(f"Found {len(files)} JSON files in {root}")
    if args.dry_run:
        print("DRY RUN - no changes will be made\n")

    ok = 0
    fail = 0
    for jf in tqdm(files, unit="file", desc="Converting"):
        if convert_and_delete(jf, dry_run=args.dry_run):
            ok += 1
        else:
            fail += 1

    print(f"\nDone: {ok} ok, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
