#!/usr/bin/env python3
"""
JSON ↔ Parquet 转换脚本，含往返测试。

用法:
  python json_parquet_convert.py convert  <input> <output>   # JSON → Parquet
  python json_parquet_convert.py revert   <input> <output>   # Parquet → JSON (compact by default)
  python json_parquet_convert.py test                         # 往返测试
  python json_parquet_convert.py convert-dir <dir> <output>   # 目录下所有 JSON → 单个 Parquet
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    print("请安装 pyarrow: pip install pyarrow")
    sys.exit(1)


def _make_table_row(data: dict, filename: str | None = None) -> dict:
    """将 data 存为 JSON 字符串，保证任意嵌套结构往返无损。"""
    row = {"data": json.dumps(data, ensure_ascii=False)}
    if filename is not None:
        row["filename"] = filename
    return row


def json_to_parquet(input_path: str, output_path: str) -> None:
    """将单个 JSON 文件转为 Parquet。"""
    input_path = Path(input_path)
    output_path = Path(output_path)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    table = pa.Table.from_pylist([_make_table_row(data)])
    pq.write_table(table, output_path)
    print(f"Converted: {input_path} -> {output_path}")


def parquet_to_json(input_path: str, output_path: str, indent: int | None = None) -> None:
    """将 Parquet 转回 JSON。默认紧凑格式与原始一致；indent=2 可 pretty-print。"""
    input_path = Path(input_path)
    output_path = Path(output_path)

    table = pq.read_table(input_path)
    records = table.to_pylist()

    def parse_data(r: dict):
        d = r["data"]
        return json.loads(d) if isinstance(d, str) else d

    if len(records) == 1:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(parse_data(records[0]), f, ensure_ascii=False, indent=indent)
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([parse_data(r) for r in records], f, ensure_ascii=False, indent=indent)

    print(f"Restored: {input_path} -> {output_path}")


def convert_dir_to_parquet(input_dir: str, output_path: str) -> None:
    """将目录下所有 JSON 合并为一个 Parquet 文件。流式处理，避免大目录占满内存。"""
    input_dir = Path(input_dir)
    output_path = Path(output_path)

    files = sorted(input_dir.glob("*.json"))
    if not files:
        print("No JSON files found")
        return

    schema = pa.schema([
        ("data", pa.string()),
        ("filename", pa.string()),
    ])
    writer = pq.ParquetWriter(output_path, schema)

    for i, jf in enumerate(files):
        with open(jf, "r", encoding="utf-8") as f:
            data = json.load(f)
        row = _make_table_row(data, jf.name)
        batch = pa.RecordBatch.from_pylist([row], schema=schema)
        writer.write_batch(batch)
        if (i + 1) % 100 == 0:
            print(f"  Processed {i + 1}/{len(files)}")

    writer.close()
    print(f"Merged {len(files)} JSON files -> {output_path}")


def revert_dir_from_parquet(input_path: str, output_dir: str) -> None:
    """从 Parquet 还原为多个 JSON 文件（需有 filename 列）。"""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    table = pq.read_table(input_path)
    records = table.to_pylist()

    for r in records:
        name = r.get("filename", f"record_{len(records)}.json")
        data = r["data"]
        data = json.loads(data) if isinstance(data, str) else data
        out = output_dir / name
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    print(f"Restored {len(records)} JSON files to {output_dir}")


def _deep_equal(a, b) -> bool:
    """递归比较两个对象是否相等。"""
    if type(a) != type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_deep_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)):
        # 允许 5 与 5.0 等价
        return a == b or (isinstance(a, (int, float)) and isinstance(b, (int, float)) and float(a) == float(b))
    return a == b


def run_roundtrip_test() -> bool:
    """往返测试：JSON -> Parquet -> JSON，验证数据一致。"""
    # 构造与真实数据结构类似的测试 JSON
    test_data = {
        "actions": ["CHECK", "BET 2", "BET 100"],
        "childrens": {
            "BET 100": {
                "actions": ["CALL", "FOLD"],
                "childrens": {
                    "CALL": {
                        "deal_number": 52,
                        "dealcards": {
                            "2c": {"deal_number": 0, "node_type": "chance_node"},
                            "2d": {"deal_number": 0, "node_type": "chance_node"},
                        },
                    },
                },
            },
        },
    }

    test_dir = Path("_parquet_test_tmp")
    test_dir.mkdir(exist_ok=True)
    json_orig = test_dir / "orig.json"
    parquet_path = test_dir / "test.parquet"
    json_restored = test_dir / "restored.json"

    try:
        # 1. 写出原始 JSON
        with open(json_orig, "w", encoding="utf-8") as f:
            json.dump(test_data, f, ensure_ascii=False)

        # 2. JSON -> Parquet（存为 JSON 字符串保证往返无损）
        table = pa.Table.from_pylist([_make_table_row(test_data)])
        pq.write_table(table, parquet_path)

        # 3. Parquet -> JSON
        table2 = pq.read_table(parquet_path)
        raw = table2.to_pylist()[0]["data"]
        restored = json.loads(raw) if isinstance(raw, str) else raw

        # 4. 写出还原的 JSON（便于人工检查）
        with open(json_restored, "w", encoding="utf-8") as f:
            json.dump(restored, f, ensure_ascii=False, indent=2)

        # 5. 深度比较
        ok = _deep_equal(test_data, restored)
        if ok:
            print("[OK] Round-trip test passed: JSON -> Parquet -> JSON data identical")
        else:
            print("[FAIL] Round-trip test failed: restored data differs from original")
            print("  Original:", json.dumps(test_data, ensure_ascii=True)[:200])
            print("  Restored:", json.dumps(restored, ensure_ascii=True)[:200])
        return ok

    finally:
        # 清理
        for f in [json_orig, parquet_path, json_restored]:
            if f.exists():
                f.unlink()
        test_dir.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description="JSON ↔ Parquet 转换与往返测试")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # convert: 单个 JSON -> Parquet
    p_convert = sub.add_parser("convert", help="单个 JSON 转 Parquet")
    p_convert.add_argument("input", help="输入 JSON 路径")
    p_convert.add_argument("output", help="输出 Parquet 路径")

    # revert: Parquet -> JSON
    p_revert = sub.add_parser("revert", help="Parquet -> JSON")
    p_revert.add_argument("input", help="Input Parquet path")
    p_revert.add_argument("output", help="Output JSON path")
    p_revert.add_argument("--indent", type=int, default=None, metavar="N", help="Pretty-print with N spaces (default: compact)")

    # convert-dir: 目录下所有 JSON -> 单个 Parquet
    p_dir = sub.add_parser("convert-dir", help="目录下所有 JSON 合并为 Parquet")
    p_dir.add_argument("input", help="JSON 所在目录")
    p_dir.add_argument("output", help="输出 Parquet 路径")

    # revert-dir: Parquet -> 多个 JSON
    p_revert_dir = sub.add_parser("revert-dir", help="Parquet 还原为多个 JSON")
    p_revert_dir.add_argument("input", help="输入 Parquet 路径")
    p_revert_dir.add_argument("output", help="输出目录")

    # test: round-trip test
    sub.add_parser("test", help="Run round-trip test (synthetic data)")

    # verify: compare original JSON vs restored JSON
    p_verify = sub.add_parser("verify", help="Verify original JSON == restored JSON")
    p_verify.add_argument("original", help="Original JSON path")
    p_verify.add_argument("restored", help="Restored JSON path (from parquet revert)")

    args = parser.parse_args()

    if args.cmd == "convert":
        json_to_parquet(args.input, args.output)
    elif args.cmd == "revert":
        parquet_to_json(args.input, args.output, indent=getattr(args, "indent", None))
    elif args.cmd == "convert-dir":
        convert_dir_to_parquet(args.input, args.output)
    elif args.cmd == "revert-dir":
        revert_dir_from_parquet(args.input, args.output)
    elif args.cmd == "test":
        ok = run_roundtrip_test()
        sys.exit(0 if ok else 1)
    elif args.cmd == "verify":
        with open(args.original, "r", encoding="utf-8") as f:
            orig = json.load(f)
        with open(args.restored, "r", encoding="utf-8") as f:
            rest = json.load(f)
        ok = _deep_equal(orig, rest)
        if ok:
            print("[OK] Original and restored JSON are identical")
        else:
            print("[FAIL] Original and restored JSON differ")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
