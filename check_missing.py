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


def parse_range_expr(expr: str, max_value: int = None) -> List[int]:
    """
    解析范围表达式
    
    支持格式:
    - 单个数字: "5"
    - 范围: "1-10"
    - 混合: "1-10,15,20-30,35"
    - 带重复: "1-10,5,8" (自动去重)
    
    Args:
        expr: 范围表达式字符串
        max_value: 最大有效值（用于验证）
        
    Returns:
        排序去重后的序号列表
    """
    indices = set()
    
    # 按逗号分割
    parts = expr.replace(" ", "").split(",")
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        if "-" in part:
            # 范围格式: "1-10"
            try:
                range_parts = part.split("-")
                if len(range_parts) == 2:
                    start = int(range_parts[0])
                    end = int(range_parts[1])
                    # 确保 start <= end
                    if start > end:
                        start, end = end, start
                    indices.update(range(start, end + 1))
                else:
                    # 处理类似 "1-10-20" 的情况，取第一个和最后一个
                    nums = [int(x) for x in range_parts if x]
                    if nums:
                        indices.update(range(min(nums), max(nums) + 1))
            except ValueError:
                print(f"[警告] 忽略无效的范围: {part}")
        else:
            # 单个数字
            try:
                indices.add(int(part))
            except ValueError:
                print(f"[警告] 忽略无效的数字: {part}")
    
    # 过滤无效值
    if max_value:
        invalid = [i for i in indices if i < 1 or i > max_value]
        if invalid:
            print(f"[警告] 以下序号超出范围 (1-{max_value})，已忽略: {sorted(invalid)[:10]}{'...' if len(invalid) > 10 else ''}")
        indices = {i for i in indices if 1 <= i <= max_value}
    else:
        # 至少过滤掉小于1的
        indices = {i for i in indices if i >= 1}
    
    return sorted(indices)


def compress_indices_to_expr(indices: List[int]) -> str:
    """
    将序号列表压缩成紧凑的范围表达式
    
    例如: [1,2,3,5,7,8,9,10,15] -> "1-3,5,7-10,15"
    
    Args:
        indices: 排序后的序号列表
        
    Returns:
        范围表达式字符串
    """
    if not indices:
        return ""
    
    indices = sorted(set(indices))
    parts = []
    start = indices[0]
    end = indices[0]
    
    for i in range(1, len(indices)):
        if indices[i] == end + 1:
            # 连续，扩展范围
            end = indices[i]
        else:
            # 不连续，保存当前范围
            if start == end:
                parts.append(str(start))
            else:
                parts.append(f"{start}-{end}")
            start = indices[i]
            end = indices[i]
    
    # 保存最后一个范围
    if start == end:
        parts.append(str(start))
    else:
        parts.append(f"{start}-{end}")
    
    return ",".join(parts)


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
    indices: List[int],
    all_boards: List[str],
    results_dir: Path = RESULTS_DIR,
    extension: str = ".json"
) -> Tuple[List[int], List[str], int]:
    """
    检查缺失的结果文件
    
    Args:
        indices: 要检查的序号列表（从1开始）
        all_boards: 所有牌面列表
        results_dir: 结果目录路径
        extension: 结果文件扩展名
        
    Returns:
        (缺失的序号列表, 缺失的牌面列表, 存在的数量)
    """
    missing_indices = []
    missing_boards = []
    exist_count = 0
    
    for i in indices:
        if i < 1 or i > len(all_boards):
            continue
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
  python check_missing.py 1-100

  # 检查所有牌面
  python check_missing.py all

  # 混合范围和单个序号
  python check_missing.py 1-50,60,70-100

  # 指定结果目录
  python check_missing.py 1-50 --results-dir ./my_results

  # 只显示缺失的序号（方便复制）
  python check_missing.py 1-100 --brief
        """
    )
    
    # 范围参数（位置参数）
    parser.add_argument("range", nargs="?", help="序号范围（如: 1-100,150,200-300 或 all）")
    parser.add_argument("--cards-file", type=str, default="cards.txt", help="牌面文件名（默认: cards.txt）")
    parser.add_argument("--results-dir", type=str, default="results", help="结果目录（默认: results）")
    parser.add_argument("--brief", action="store_true", help="简洁输出，只显示缺失的序号（紧凑格式）")
    parser.add_argument("--extension", type=str, default=".json", help="结果文件扩展名（默认: .json）")
    
    args = parser.parse_args()
    
    # 参数检查
    if not args.range:
        parser.print_help()
        print("\n[错误] 请指定序号范围，如: 1-100 或 1-50,60,70-100 或 all")
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
    
    # 读取牌面列表
    all_boards = read_cards_from_txt(cards_file)
    total_boards = len(all_boards)
    
    # 解析范围表达式
    if args.range.lower() == "all":
        indices = list(range(1, total_boards + 1))
    else:
        indices = parse_range_expr(args.range, max_value=total_boards)
    
    if not indices:
        print("[错误] 没有有效的序号")
        sys.exit(1)
    
    check_count = len(indices)
    
    if not args.brief:
        print("=" * 60)
        print("检查缺失的求解结果")
        print("=" * 60)
        print(f"牌面文件: {cards_file}")
        print(f"结果目录: {results_dir}")
        if check_count <= 20:
            print(f"检查范围: {compress_indices_to_expr(indices)} (共 {check_count} 个)")
        else:
            print(f"检查范围: {indices[0]}-{indices[-1]} (共 {check_count} 个序号)")
        print(f"总牌面数: {total_boards}")
        print("=" * 60)
    
    # 执行检查
    missing_indices, missing_boards, exist_count = check_missing(
        indices=indices,
        all_boards=all_boards,
        results_dir=results_dir,
        extension=args.extension
    )
    
    missing_count = len(missing_indices)
    
    if args.brief:
        # 简洁输出（紧凑格式）
        if missing_indices:
            print(compress_indices_to_expr(missing_indices))
        else:
            print("无缺失")
    else:
        # 详细输出
        print(f"\n[统计结果]")
        print(f"   已存在: {exist_count}/{check_count}")
        print(f"   缺失:   {missing_count}/{check_count}")
        print(f"   完成率: {exist_count/check_count*100:.1f}%")
        
        if missing_indices:
            print(f"\n[缺失的牌面] ({missing_count} 个):")
            print("-" * 60)
            
            # 使用紧凑格式显示
            missing_expr = compress_indices_to_expr(missing_indices)
            print(f"   序号: {missing_expr}")
            
            print("-" * 60)
            print(f"\n[缺失详情]")
            # 限制显示数量，避免输出过长
            display_count = min(50, len(missing_indices))
            for idx, board in zip(missing_indices[:display_count], missing_boards[:display_count]):
                print(f"   [{idx}] {board}")
            if len(missing_indices) > display_count:
                print(f"   ... 还有 {len(missing_indices) - display_count} 个未显示")
            
            # 输出可以直接使用的命令
            print(f"\n[重新求解] 可以使用以下命令:")
            print(f"   python auto_run_solver.py {missing_expr}")
        else:
            print(f"\n[完成] 所有结果文件都存在！")
        
        print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
