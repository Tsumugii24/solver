#!/usr/bin/env python3
"""
自动化流水线：Solver → JSON → Parquet → Hugging Face

转换触发条件：结果目录下积累的 JSON 数量 >= batch_size 时，才执行转换+上传。
（不按求解批次触发，避免部分任务失败时逻辑混乱）

用法:
  python run_pipeline.py 1-20                    # 求解 1-20，每 5 个一批转换+上传
  python run_pipeline.py 1-20 --batch-size 5    # 同上
  python run_pipeline.py 1,5,10,15,20            # 指定序号
  python run_pipeline.py 1-20 --no-upload       # 只求解+转换，不上传
  python run_pipeline.py 1-20 --convert-only     # 仅转换已有 JSON 并上传（不跑 solver）

环境变量:
  HF_TOKEN 或 HUGGINGFACE_HUB_TOKEN: 未登录时自动用此 token 登录
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
RESULTS_DIR = SCRIPT_DIR / "results"
UPLOAD_DIR = SCRIPT_DIR / "upload"


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
    from auto_run_solver import read_cards, CONFIG_DIR
    cards_path = CONFIG_DIR / "cards.txt"
    if not cards_path.exists():
        return 1755  # 默认
    boards = read_cards(cards_path)
    return len(boards)


def _count_json() -> int:
    """统计 results 目录下的 JSON 文件数量"""
    if not RESULTS_DIR.is_dir():
        return 0
    return len(list(RESULTS_DIR.glob("*.json")))


def _delete_parquets() -> int:
    """删除 results 目录下已上传的 parquet 文件，返回删除数量。"""
    if not RESULTS_DIR.is_dir():
        return 0
    deleted = 0
    for f in RESULTS_DIR.glob("*.parquet"):
        try:
            f.unlink()
            deleted += 1
        except OSError as e:
            print(f"[警告] 无法删除 {f.name}: {e}")
    return deleted


def _do_convert_and_upload(upload: bool) -> bool:
    """执行 JSON→Parquet 转换，可选上传。上传成功后删除 parquet 以节省空间。返回是否成功。"""
    ok = _run([sys.executable, str(UPLOAD_DIR / "batch_json_to_parquet.py"), str(RESULTS_DIR)])
    if ok and upload:
        ok = _run([sys.executable, str(UPLOAD_DIR / "upload_to_hf.py"), str(RESULTS_DIR)])
        if ok:
            n = _delete_parquets()
            if n > 0:
                print(f"[清理] 已删除 {n} 个已上传的 parquet 文件")
    return ok


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
            print("[错误] 未登录 Hugging Face，且未设置 HF_TOKEN 环境变量")
            print("  请运行: huggingface-cli login  或设置 HF_TOKEN")
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
        description="Solver → JSON → Parquet → Hugging Face 自动化流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("range", help="序号范围，如 1-20 或 1,5,10,15,20 或 all")
    parser.add_argument("--batch-size", "-b", type=int, default=5,
                        help="results 目录下 JSON 积累到此数时触发转换+上传（默认 5）")
    parser.add_argument("--no-upload", action="store_true",
                        help="只求解+转换，不上传到 HF")
    parser.add_argument("--convert-only", action="store_true",
                        help="仅转换已有 JSON 并上传，不跑 solver")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅预览，不执行")
    # 透传给 auto_run_solver
    parser.add_argument("--file", type=str, default="cards.txt")
    parser.add_argument("--thread-num", type=int, default=-1)
    parser.add_argument("--max-iteration", type=int, default=300)
    args = parser.parse_args()

    total = _get_total_boards()
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
    print("自动化流水线: Solver → Parquet → Hugging Face")
    print("=" * 60)
    print(f"总任务: {len(indices)} 个牌面")
    print(f"求解批次: {len(batches)}，每批最多 {batch_size} 个")
    print(f"转换触发: results 下 JSON 数量 >= {batch_size} 时转换+上传")
    if has_remainder:
        print(f"  （最后一批含 {last_batch_size} 个；结束时若有剩余 JSON 也会转换）")
    print(f"结果目录: {RESULTS_DIR}")
    if args.dry_run:
        print("\n[DRY RUN] 仅预览，不执行")
        for i, batch in enumerate(batches, 1):
            expr = _compress_indices(batch)
            n = len(batch)
            suffix = f" ({n} 个)" if has_remainder and i == len(batches) else ""
            print(f"  Batch {i}: {expr}{suffix}")
        sys.exit(0)

    # 需要上传时，先检查 HF 登录
    if not args.no_upload:
        if not _ensure_hf_logged_in():
            print("[错误] 无法上传：请先登录 HF 或设置 HF_TOKEN")
            sys.exit(1)

    if args.convert_only:
        print("\n[模式] 仅转换+上传（不跑 solver）")
        _run([sys.executable, str(UPLOAD_DIR / "batch_json_to_parquet.py"), str(RESULTS_DIR)])
        if not args.no_upload:
            _run([sys.executable, str(UPLOAD_DIR / "upload_to_hf.py"), str(RESULTS_DIR)])
        print("\n[完成]")
        sys.exit(0)

    for i, batch in enumerate(batches, 1):
        expr = _compress_indices(batch)
        print(f"\n{'='*60}")
        print(f"[Batch {i}/{len(batches)}] 求解: {expr}")
        print("=" * 60)

        # 1. Run solver
        solver_cmd = [
            sys.executable, str(SCRIPT_DIR / "auto_run_solver.py"),
            expr,
            "--file", args.file,
            "--thread-num", str(args.thread_num),
            "--max-iteration", str(args.max_iteration),
        ]
        if not _run(solver_cmd):
            print(f"[失败] Solver 批 {i} 未完全成功，继续下一批")

        # 2. 检查 results 下积累的 JSON 数量，达到 batch_size 则转换+上传
        json_count = _count_json()
        if json_count >= batch_size:
            print(f"\n[触发] results 下已积累 {json_count} 个 JSON (>= {batch_size})")
            print("[转换] JSON → Parquet")
            _do_convert_and_upload(not args.no_upload)
            if not args.no_upload:
                print("[上传] Hugging Face")

    # 3. 结束时处理剩余 JSON（无法整除或部分失败导致的数量不足）
    json_count = _count_json()
    if json_count > 0:
        print(f"\n[收尾] results 下剩余 {json_count} 个 JSON，转换并上传")
        _do_convert_and_upload(not args.no_upload)
        if not args.no_upload:
            print("[上传] Hugging Face")

    print("\n" + "=" * 60)
    print("[完成] 流水线执行完毕")
    print("=" * 60)


if __name__ == "__main__":
    main()
