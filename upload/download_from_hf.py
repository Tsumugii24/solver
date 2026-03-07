#!/usr/bin/env python3
"""
从 HuggingFace Dataset 下载指定牌面的 Parquet 文件到 cache 目录。

用法:
  python download_from_hf.py AcAdAh AcAdKc
  python download_from_hf.py AcAdAh --repo Tsumugii/gto-srp-100bb-v1
  python download_from_hf.py 1 2 3 --cards configs/cards.txt   # 按序号
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

try:
    from huggingface_hub import HfApi, hf_hub_download
except ImportError:
    print("pip install -U huggingface_hub")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_REPO = "Tsumugii/gto-srp-100bb-v1"
DEFAULT_CACHE = "cache"


def parse_repo_id(repo_arg: str) -> str:
    """从 URL 或 repo_id 解析出标准 repo_id"""
    repo_arg = repo_arg.strip()
    m = re.search(
        r"huggingface\.co/datasets/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)",
        repo_arg,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    if "/" in repo_arg and " " not in repo_arg:
        return repo_arg
    raise ValueError(f"无法解析 repo: {repo_arg}")


def board_to_filename(board: str) -> str:
    """牌面转文件名（去逗号）"""
    return board.replace(",", "")


def find_file_in_repo(api: HfApi, repo_id: str, board: str) -> Optional[str]:
    """在 repo 中查找牌面对应的 parquet 文件路径（大小写不敏感）"""
    files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    fname = board_to_filename(board)
    fname_lower = fname.lower()
    for f in files:
        if f.lower().endswith(".parquet"):
            stem = Path(f).stem
            if stem.lower() == fname_lower:
                return f
    return None


def read_boards_from_cards(cards_path: Path) -> list[str]:
    """从 cards.txt 读取牌面列表"""
    if not cards_path.exists():
        raise FileNotFoundError(f"文件不存在: {cards_path}")
    boards = []
    with open(cards_path, "r", encoding="utf-8") as f:
        for line in f:
            b = line.strip()
            if b:
                boards.append(b)
    return boards


def download_board_from_hf(
    board: str,
    cache_dir: str = DEFAULT_CACHE,
    repo_id: str = DEFAULT_REPO,
) -> Optional[Path]:
    """
    从 HuggingFace 下载单个牌面的 Parquet 到指定目录。

    Args:
        board: 牌面名称，如 Ac2c2d 或 Ac,Ad,Ah
        cache_dir: 下载目录
        repo_id: HuggingFace dataset repo

    Returns:
        下载后的本地路径，失败返回 None
    """
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        return None

    board = board.strip()
    fname = board_to_filename(board)
    cache_path = Path(cache_dir).resolve()
    cache_path.mkdir(parents=True, exist_ok=True)

    try:
        api = HfApi()
        remote_path = find_file_in_repo(api, repo_id, board)
        if not remote_path:
            return None
        local_path = hf_hub_download(
            repo_id=repo_id,
            filename=remote_path,
            repo_type="dataset",
            local_dir=str(cache_path),
            local_dir_use_symlinks=False,
            force_download=False,
        )
        return Path(local_path)
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 HuggingFace Dataset 下载指定牌面的 Parquet 到 cache",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python download_from_hf.py AcAdAh AcAdKc
  python download_from_hf.py 1 2 3 --cards configs/cards.txt
  python download_from_hf.py AcAdAh --cache-dir ./my_cache
        """,
    )
    parser.add_argument(
        "boards",
        nargs="+",
        help="牌面名称（如 AcAdAh）或序号（需配合 --cards）",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=DEFAULT_REPO,
        help=f"Dataset repo（默认: {DEFAULT_REPO}）",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=DEFAULT_CACHE,
        help=f"下载目录（默认: {DEFAULT_CACHE}）",
    )
    parser.add_argument(
        "--cards",
        type=str,
        default=None,
        help="cards.txt 路径，指定后 boards 可填序号",
    )
    args = parser.parse_args()

    try:
        repo_id = parse_repo_id(args.repo)
    except ValueError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    cache_dir = Path(args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 解析牌面列表
    if args.cards:
        cards_path = Path(args.cards)
        if not cards_path.is_absolute():
            cards_path = SCRIPT_DIR.parent / args.cards
        all_boards = read_boards_from_cards(cards_path)
        boards = []
        for b in args.boards:
            try:
                idx = int(b)
                if 1 <= idx <= len(all_boards):
                    boards.append(all_boards[idx - 1])
                else:
                    print(f"[警告] 序号 {idx} 超出范围 1-{len(all_boards)}，已忽略")
            except ValueError:
                boards.append(b)
    else:
        boards = [b.strip() for b in args.boards if b.strip()]

    if not boards:
        print("[错误] 没有有效的牌面")
        sys.exit(1)

    api = HfApi()
    print(f"Repo: https://huggingface.co/datasets/{repo_id}")
    print(f"Cache: {cache_dir}")
    print(f"牌面: {len(boards)} 个")
    print("-" * 50)

    ok = 0
    fail = 0
    for board in boards:
        fname = board_to_filename(board)
        remote_path = find_file_in_repo(api, repo_id, board)
        if not remote_path:
            print(f"  [跳过] {board} - 未在 repo 中找到")
            fail += 1
            continue

        try:
            local_path = hf_hub_download(
                repo_id=repo_id,
                filename=remote_path,
                repo_type="dataset",
                local_dir=str(cache_dir),
                local_dir_use_symlinks=False,
                force_download=False,
            )
            print(f"  [OK] {board} -> {Path(local_path).name}")
            ok += 1
        except Exception as e:
            print(f"  [失败] {board}: {e}")
            fail += 1

    print("-" * 50)
    print(f"完成: {ok} 成功, {fail} 失败")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
