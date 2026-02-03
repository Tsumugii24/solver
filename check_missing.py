"""
检查缺失的求解结果
检查指定范围内哪些牌面的结果文件不存在
"""

import argparse
import sys
from pathlib import Path
from typing import List, Tuple


# ==================== 配置 ====================
SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = SCRIPT_DIR / "configs"
RESULTS_DIR = SCRIPT_DIR / "results"
CARDS_FILE = CONFIG_DIR / "cards.txt"
# =============================================


def read_cards_from_txt(txt_path: Path) -> List[str]:
    """从 txt 文件读取牌面列表"""
    if not txt_path.exists():
        raise FileNotFoundError(f"文件不存在: {txt_path}")
    
    boards = []
    with open(txt_path, 'r', encoding='utf-8') as f:
        for line in f:
            board = line.strip()
            if board:
                boards.append(board)
    return boards


def board_to_filename(board: str) -> str:
    """将牌面转换为文件名（去除逗号）"""
    return board.replace(",", "")


def check_missing(
    start: int,
    end: int,
    cards_file: Path = CARDS_FILE,
    results_dir: Path = RESULTS_DIR,
    extension: str = ".json"
) -> Tuple[List[int], List[str], int]:
    """
    检查缺失的结果文件
    
    Args:
        start: 起始序号（从1开始）
        end: 结束序号
        cards_file: 牌面文件路径
        results_dir: 结果目录路径
        extension: 结果文件扩展名
        
    Returns:
        (缺失的序号列表, 缺失的牌面列表, 存在的数量)
    """
    # 读取牌面列表
    all_boards = read_cards_from_txt(cards_file)
    
    # 验证范围
    if start < 1:
        start = 1
    if end > len(all_boards):
        end = len(all_boards)
    
    missing_indices = []
    missing_boards = []
    exist_count = 0
    
    for i in range(start, end + 1):
        board = all_boards[i - 1]  # 转换为0-based索引
        filename = board_to_filename(board) + extension
        filepath = results_dir / filename
        
        if filepath.exists():
            exist_count += 1
        else:
            missing_indices.append(i)
            missing_boards.append(board)
    
    return missing_indices, missing_boards, exist_count


def main():
    parser = argparse.ArgumentParser(
        description="检查缺失的求解结果文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 检查第 1 到第 100 个牌面的结果
  python check_missing.py --start 1 --end 100

  # 检查所有牌面
  python check_missing.py --all

  # 指定结果目录
  python check_missing.py --start 1 --end 50 --results-dir ./my_results

  # 只显示缺失的序号（方便复制）
  python check_missing.py --start 1 --end 100 --brief
        """
    )
    
    parser.add_argument("--start", type=int, help="起始序号（从1开始）")
    parser.add_argument("--end", type=int, help="结束序号")
    parser.add_argument("--all", action="store_true", help="检查所有牌面")
    parser.add_argument("--cards-file", type=str, default="cards.txt", help="牌面文件名（默认: cards.txt）")
    parser.add_argument("--results-dir", type=str, default="results", help="结果目录（默认: results）")
    parser.add_argument("--brief", action="store_true", help="简洁输出，只显示缺失的序号")
    parser.add_argument("--extension", type=str, default=".json", help="结果文件扩展名（默认: .json）")
    
    args = parser.parse_args()
    
    # 参数检查
    if not args.all and (args.start is None or args.end is None):
        parser.print_help()
        print("\n[错误] 请指定 --start 和 --end，或使用 --all")
        sys.exit(1)
    
    # 路径处理
    cards_file = CONFIG_DIR / args.cards_file
    results_dir = SCRIPT_DIR / args.results_dir
    
    # 检查文件和目录
    if not cards_file.exists():
        print(f"[错误] 牌面文件不存在: {cards_file}")
        sys.exit(1)
    
    if not results_dir.exists():
        print(f"[警告] 结果目录不存在: {results_dir}")
        results_dir.mkdir(parents=True, exist_ok=True)
    
    # 读取牌面数量
    all_boards = read_cards_from_txt(cards_file)
    total_boards = len(all_boards)
    
    # 确定范围
    if args.all:
        start = 1
        end = total_boards
    else:
        start = max(1, args.start)
        end = min(args.end, total_boards)
    
    if not args.brief:
        print("=" * 60)
        print("检查缺失的求解结果")
        print("=" * 60)
        print(f"牌面文件: {cards_file}")
        print(f"结果目录: {results_dir}")
        print(f"检查范围: {start} - {end} (共 {end - start + 1} 个)")
        print(f"总牌面数: {total_boards}")
        print("=" * 60)
    
    # 执行检查
    missing_indices, missing_boards, exist_count = check_missing(
        start=start,
        end=end,
        cards_file=cards_file,
        results_dir=results_dir,
        extension=args.extension
    )
    
    check_count = end - start + 1
    missing_count = len(missing_indices)
    
    if args.brief:
        # 简洁输出
        if missing_indices:
            print(",".join(map(str, missing_indices)))
        else:
            print("无缺失")
    else:
        # 详细输出
        print(f"\n📊 统计结果:")
        print(f"   已存在: {exist_count}/{check_count}")
        print(f"   缺失:   {missing_count}/{check_count}")
        print(f"   完成率: {exist_count/check_count*100:.1f}%")
        
        if missing_indices:
            print(f"\n❌ 缺失的牌面 ({missing_count} 个):")
            print("-" * 60)
            
            # 分组显示（每行10个）
            for i in range(0, len(missing_indices), 10):
                batch = missing_indices[i:i+10]
                print(f"   序号: {', '.join(map(str, batch))}")
            
            print("-" * 60)
            print(f"\n📋 缺失详情:")
            for idx, board in zip(missing_indices, missing_boards):
                print(f"   [{idx}] {board}")
            
            # 输出可以直接使用的命令
            print(f"\n💡 可以使用以下命令重新求解缺失的牌面:")
            
            # 检查是否连续
            if len(missing_indices) > 0:
                # 找出连续区间
                ranges = []
                range_start = missing_indices[0]
                range_end = missing_indices[0]
                
                for i in range(1, len(missing_indices)):
                    if missing_indices[i] == range_end + 1:
                        range_end = missing_indices[i]
                    else:
                        ranges.append((range_start, range_end))
                        range_start = missing_indices[i]
                        range_end = missing_indices[i]
                ranges.append((range_start, range_end))
                
                # 输出命令
                for r_start, r_end in ranges:
                    if r_start == r_end:
                        print(f"   python auto_run_solver.py --start {r_start} --end {r_start}")
                    else:
                        print(f"   python auto_run_solver.py --start {r_start} --end {r_end}")
        else:
            print(f"\n✅ 所有结果文件都存在！")
        
        print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
