"""
检查缺失的求解结果（以 HuggingFace Dataset 为参照）

以 HuggingFace 上的 dataset repo 作为「已存在」的牌面集合，对比 cards.txt 找出缺失的牌面。
可选：从缺失牌面中随机抽取若干序号，用于 auto_run_solver.py。

用法:
  python check_missing.py https://huggingface.co/datasets/Tsumugii/gto-srp-100bb-v1
  python check_missing.py Tsumugii/gto-srp-100bb-v1 --gen-random 10
  python check_missing.py Tsumugii/gto-srp-100bb-v1 --gen-random 10 --run
"""

import argparse
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Set, Tuple

try:
    from huggingface_hub import HfApi
except ImportError:
    HfApi = None  # type: ignore


# ==================== 配置 ====================
SCRIPT_DIR = Path(__file__).parent.resolve()
CARDS_DIR = SCRIPT_DIR / "cards"
CARDS_FILE = CARDS_DIR / "cards.txt"
# =============================================


def parse_repo_id(repo_arg: str) -> str:
    """
    从 URL 或 repo_id 解析出标准 repo_id
    例如: https://huggingface.co/datasets/Tsumugii/gto-srp-100bb-v1 -> Tsumugii/gto-srp-100bb-v1
    """
    repo_arg = repo_arg.strip()
    # URL 格式
    m = re.search(
        r"huggingface\.co/datasets/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)",
        repo_arg,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # 已是 owner/repo 格式
    if "/" in repo_arg and " " not in repo_arg:
        return repo_arg
    raise ValueError(f"无法解析 repo: {repo_arg}，请使用 URL 或 owner/repo 格式")


def list_boards_in_hf_dataset(repo_id: str) -> Set[str]:
    """
    列出 HuggingFace dataset 中已有的牌面（从 parquet 文件名提取）
    文件名格式: {board}.parquet，board 为牌面（无逗号）
    """
    if HfApi is None:
        raise RuntimeError("请安装 huggingface_hub: pip install huggingface_hub")

    api = HfApi()
    files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")

    boards: Set[str] = set()
    for f in files:
        if f.lower().endswith(".parquet"):
            stem = Path(f).stem
            if stem:
                boards.add(stem)
    return boards


def read_cards_from_txt(txt_path: Path) -> List[str]:
    """从 txt 文件读取牌面列表"""
    if not txt_path.exists():
        raise FileNotFoundError(f"文件不存在: {txt_path}")

    boards = []
    with open(txt_path, "r", encoding="utf-8") as f:
        for line in f:
            board = line.strip()
            if board:
                boards.append(board)
    return boards


def board_to_filename(board: str) -> str:
    """将牌面转换为文件名（去除逗号）"""
    return board.replace(",", "")


def compress_indices_to_expr(indices: List[int]) -> str:
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


def check_missing_from_hf(
    repo_id: str,
    all_boards: List[str],
) -> Tuple[List[int], List[str], int]:
    """
    以 HuggingFace dataset 为参照，检查缺失的牌面

    Returns:
        (缺失的序号列表, 缺失的牌面列表, 已存在的数量)
    """
    boards_in_hf = list_boards_in_hf_dataset(repo_id)
    missing_indices: List[int] = []
    missing_boards: List[str] = []
    exist_count = 0

    for i, board in enumerate(all_boards, start=1):
        fname = board_to_filename(board)
        if fname in boards_in_hf:
            exist_count += 1
        else:
            missing_indices.append(i)
            missing_boards.append(board)

    return missing_indices, missing_boards, exist_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="以 HuggingFace Dataset 为参照，检查缺失的牌面；可选生成随机缺失序号",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 检查缺失
  python check_missing.py https://huggingface.co/datasets/Tsumugii/gto-srp-100bb-v1

  # 从缺失中随机抽 10 个序号（输出紧凑格式，可直接用于 auto_run_solver）
  python check_missing.py Tsumugii/gto-srp-100bb-v1 --gen-random 10

  # 抽 10 个并直接执行 auto_run_solver
  python check_missing.py Tsumugii/gto-srp-100bb-v1 --gen-random 10 --run

  # 简洁输出
  python check_missing.py Tsumugii/gto-srp-100bb-v1 --brief
        """,
    )

    parser.add_argument(
        "repo",
        nargs="?",
        help="HuggingFace dataset URL 或 repo_id（如 Tsumugii/gto-srp-100bb-v1）",
    )
    parser.add_argument(
        "--cards-file",
        type=str,
        default="cards.txt",
        help="牌面文件名（默认: cards.txt）",
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help="简洁输出，只显示缺失的序号（紧凑格式）",
    )
    parser.add_argument(
        "--gen-random",
        type=int,
        metavar="N",
        default=None,
        help="从缺失牌面中随机抽取 N 个序号并输出（可与 --run 配合）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="随机种子（用于复现）",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="与 --gen-random 配合：抽取后直接执行 auto_run_solver.py",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="与 --gen-random 配合：输出逗号分隔的原始列表，不压缩范围",
    )

    args = parser.parse_args()

    if not args.repo:
        parser.print_help()
        print("\n[错误] 请指定 HuggingFace dataset repo，如: Tsumugii/gto-srp-100bb-v1")
        sys.exit(1)

    try:
        repo_id = parse_repo_id(args.repo)
    except ValueError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    cards_file = CARDS_DIR / args.cards_file
    if not cards_file.exists():
        print(f"[错误] 牌面文件不存在: {cards_file}")
        sys.exit(1)

    all_boards = read_cards_from_txt(cards_file)
    total_boards = len(all_boards)

    if not args.brief:
        print("=" * 60)
        print("检查缺失的求解结果（以 HuggingFace Dataset 为参照）")
        print("=" * 60)
        print(f"Dataset: https://huggingface.co/datasets/{repo_id}")
        print(f"牌面文件: {cards_file}")
        print(f"总牌面数: {total_boards}")
        print("=" * 60)

    try:
        missing_indices, missing_boards, exist_count = check_missing_from_hf(
            repo_id=repo_id,
            all_boards=all_boards,
        )
    except Exception as e:
        print(f"[错误] 获取 dataset 文件列表失败: {e}")
        if "huggingface_hub" in str(e).lower() or HfApi is None:
            print("  请安装: pip install huggingface_hub")
        sys.exit(1)

    missing_count = len(missing_indices)

    # --gen-random: 从缺失中随机抽取
    if args.gen_random is not None:
        n = args.gen_random
        if n < 1:
            print("[错误] --gen-random 必须 >= 1")
            sys.exit(1)
        if not missing_indices:
            print("[提示] 无缺失牌面，无法抽取")
            sys.exit(0)
        if n > missing_count:
            n = missing_count
            if not args.brief:
                print(f"[提示] 缺失仅 {missing_count} 个，已全部抽取")

        if args.seed is not None:
            random.seed(args.seed)

        sampled = random.sample(missing_indices, n)
        if args.simple:
            expr = ",".join(str(i) for i in sorted(sampled))
        else:
            expr = compress_indices_to_expr(sampled)

        print(expr)

        if args.run:
            cmd = [sys.executable, str(SCRIPT_DIR / "auto_run_solver.py"), expr]
            if not args.brief:
                print(f"\n[执行] {' '.join(cmd)}", file=sys.stderr)
            subprocess.run(cmd, cwd=str(SCRIPT_DIR))
        return

    # 默认：输出检查结果
    if args.brief:
        if missing_indices:
            print(compress_indices_to_expr(missing_indices))
        else:
            print("无缺失")
        return

    print(f"\n[统计结果]")
    print(f"   Dataset 中已存在: {exist_count}/{total_boards}")
    print(f"   缺失: {missing_count}/{total_boards}")
    if total_boards > 0:
        print(f"   完成率: {exist_count / total_boards * 100:.1f}%")

    if missing_indices:
        missing_expr = compress_indices_to_expr(missing_indices)
        print(f"\n[缺失的牌面] ({missing_count} 个)")
        print("-" * 60)
        print(f"   序号: {missing_expr}")
        print("-" * 60)
        print(f"\n[缺失详情]")
        display_count = min(50, len(missing_indices))
        for idx, board in zip(
            missing_indices[:display_count], missing_boards[:display_count]
        ):
            print(f"   [{idx}] {board}")
        if len(missing_indices) > display_count:
            print(f"   ... 还有 {len(missing_indices) - display_count} 个未显示")

        print(f"\n[求解缺失] 可使用:")
        print(f"   python check_missing.py {repo_id} --gen-random 10 --run")
        print(f"   # 或一次性求解全部:")
        print(f"   python auto_run_solver.py {missing_expr}")
    else:
        print(f"\n[完成] 所有牌面均已在 Dataset 中！")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    # # 从缺失中随机抽 10 个序号（输出紧凑格式）
    # python check_missing.py Tsumugii/gto-srp-100bb-v1 --gen-random 10

    # # 抽 10 个并直接执行 auto_run_solver
    # python check_missing.py Tsumugii/gto-srp-100bb-v1 --gen-random 10 --run

    # # 指定随机种子
    # python check_missing.py Tsumugii/gto-srp-100bb-v1 --gen-random 10 --seed 42

    # # 输出逗号分隔列表（不压缩范围）
    # python check_missing.py Tsumugii/gto-srp-100bb-v1 --gen-random 5 --simple
    main()
