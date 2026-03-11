#!/usr/bin/env python3
"""
从 Parquet 还原为 JSON。可手动输入牌面（如 2c2d2h、AcKd）指定要还原的文件。

用法:
  python restore_parquet_to_json.py 2c2d2h
  python restore_parquet_to_json.py 2c2d2h 3c3d3h
  python restore_parquet_to_json.py                    # 交互输入
  python restore_parquet_to_json.py --dir /path/to/data
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from solver_result_io import load_solver_result_from_parquet


def restore(parquet_path: Path, output_path: Path | None = None, indent: int | None = None) -> bool:
    """Restore Parquet to JSON. Returns True on success."""
    if not parquet_path.exists():
        print(f"Not found: {parquet_path}")
        return False

    out = output_path or parquet_path.with_suffix(".json")
    data = load_solver_result_from_parquet(parquet_path)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)

    print(f"Restored: {parquet_path.name} -> {out.name}")
    return True


def normalize_name(s: str) -> str:
    """2c2d2h / 2c2d2h.json / 2c2d2h.parquet -> 2c2d2h"""
    s = s.strip()
    for ext in (".json", ".parquet"):
        if s.lower().endswith(ext):
            s = s[: -len(ext)]
    return s


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore Parquet to JSON by hand/card input")
    parser.add_argument("hands", nargs="*", help="Hand(s) to restore, e.g. 2c2d2h AcKd")
    parser.add_argument("--dir", "-d", default=".", help="Directory with parquet files")
    parser.add_argument("--indent", type=int, default=None, metavar="N", help="Pretty-print with N spaces")
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}")
        sys.exit(1)

    hands = args.hands
    if not hands:
        hands_str = input("Input hand(s), e.g. 2c2d2h or 2c2d2h AcKd: ").strip()
        if not hands_str:
            print("No input")
            sys.exit(0)
        hands = hands_str.split()

    ok = 0
    for h in hands:
        name = normalize_name(h)
        parquet_path = root / f"{name}.parquet"
        if restore(parquet_path, indent=args.indent):
            ok += 1

    sys.exit(0 if ok == len(hands) else 1)


if __name__ == "__main__":
    # # 命令行指定牌面
    # python restore_parquet_to_json.py 2c2d2h
    # python restore_parquet_to_json.py 2c2d2h 3c3d3h AcKd

    # # 交互输入（无参数时）
    # python restore_parquet_to_json.py

    # # 指定目录
    # python restore_parquet_to_json.py --dir d:\results 2c2d2h

    # # 带缩进输出
    # python restore_parquet_to_json.py --indent 2 2c2d2h
    main()
