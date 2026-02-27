"""
从 1-1755 中随机挑选 n 个序号，输出格式可直接用于 python auto_run_solver.py

用法:
  python gen_random_indices.py 10              # 随机选 10 个
  python gen_random_indices.py 10 --run       # 选 10 个并直接执行 auto_run_solver
  python gen_random_indices.py 10 --max 500   # 从 1-500 中选 10 个
"""

import argparse
import random
import subprocess
import sys
from pathlib import Path


def compress_indices(indices: list) -> str:
    """
    将序号列表压缩成紧凑的范围表达式
    例如: [1,2,3,5,7,8,9,10,15] -> "1-3,5,7-10,15"
    """
    if not indices:
        return ""
    indices = sorted(set(indices))
    parts = []
    start = indices[0]
    end = indices[0]
    for i in range(1, len(indices)):
        if indices[i] == end + 1:
            end = indices[i]
        else:
            parts.append(f"{start}-{end}" if start != end else str(start))
            start = end = indices[i]
    parts.append(f"{start}-{end}" if start != end else str(start))
    return ",".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="从 1-1755 中随机挑选 n 个序号，输出可直接用于 auto_run_solver.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python gen_random_indices.py 10
      # 输出: 1-3,5,7-10,15  （随机 10 个序号的紧凑格式）

  python gen_random_indices.py 10 --run
      # 选 10 个并直接执行: python auto_run_solver.py <range>

  python gen_random_indices.py 5 --max 100
      # 从 1-100 中随机选 5 个
        """
    )
    parser.add_argument("n", type=int, help="要挑选的序号数量")
    parser.add_argument("--max", type=int, default=1755, help="序号上限（默认 1755）")
    parser.add_argument("--min", type=int, default=1, help="序号下限（默认 1）")
    parser.add_argument("--seed", type=int, default=None, help="随机种子（可选，用于复现）")
    parser.add_argument("--run", action="store_true", help="直接执行 auto_run_solver.py")
    parser.add_argument("--simple", action="store_true", help="输出逗号分隔的原始列表，不压缩范围")
    args = parser.parse_args()

    if args.n < 1:
        print("[错误] n 必须 >= 1", file=sys.stderr)
        sys.exit(1)
    pool_size = args.max - args.min + 1
    if args.n > pool_size:
        print(f"[错误] n={args.n} 超过可用范围大小 {pool_size} (min={args.min}, max={args.max})", file=sys.stderr)
        sys.exit(1)

    if args.seed is not None:
        random.seed(args.seed)

    indices = random.sample(range(args.min, args.max + 1), args.n)
    if args.simple:
        expr = ",".join(str(i) for i in sorted(indices))
    else:
        expr = compress_indices(indices)

    print(expr)

    if args.run:
        script_dir = Path(__file__).parent
        cmd = [sys.executable, str(script_dir / "auto_run_solver.py"), expr]
        print(f"\n[执行] {' '.join(cmd)}", file=sys.stderr)
        subprocess.run(cmd, cwd=str(script_dir))


if __name__ == "__main__":
    main()
