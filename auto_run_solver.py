"""
TexasSolver Console 自动批量求解脚本
从 cards.txt 或 cards.xlsx 读取牌面配置，自动生成配置文件并串行求解
支持容错机制和详细统计信息
"""

import subprocess
import os
import sys
import time
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import argparse


# ==================== 配置 ====================
# 脚本所在目录
SCRIPT_DIR = Path(__file__).parent.resolve()
# 求解器路径（根据操作系统选择）
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    SOLVER_EXE = str(SCRIPT_DIR / "build" / "console_solver.exe")
else:
    SOLVER_EXE = str(SCRIPT_DIR / "install" / "console_solver")
# Resources 目录
RESOURCE_DIR = str(SCRIPT_DIR / "resources")
# 配置文件目录
CONFIG_DIR = SCRIPT_DIR / "configs"
# 结果输出目录
RESULTS_DIR = SCRIPT_DIR / "results"
# 牌面文件路径（优先使用 txt）
CARDS_FILE = CONFIG_DIR / "cards.txt"
CARDS_EXCEL = CONFIG_DIR / "cards.xlsx"
# 超时时间（秒）
TIMEOUT = 7200  # 2小时
# 最大重试次数
MAX_RETRIES = 3
# =============================================


# ==================== 配置模板 ====================
CONFIG_TEMPLATE = """set_pot {pot}
set_effective_stack {effective_stack}
set_board {board}
set_range_oop {range_oop}
set_range_ip {range_ip}
set_bet_sizes oop,flop,bet,33
set_bet_sizes oop,flop,raise,50,100
set_bet_sizes oop,flop,allin
set_bet_sizes ip,flop,bet,30,50,70
set_bet_sizes ip,flop,raise,50
set_bet_sizes ip,flop,allin
set_bet_sizes oop,turn,bet,25,50,75,150
set_bet_sizes oop,turn,raise,150
set_bet_sizes oop,turn,donk,33
set_bet_sizes oop,turn,allin
set_bet_sizes ip,turn,bet,50,80,150
set_bet_sizes ip,turn,raise,75
set_bet_sizes ip,turn,allin
set_bet_sizes oop,river,bet,30,50,75,125,200
set_bet_sizes oop,river,raise,75,175
set_bet_sizes oop,river,donk,33
set_bet_sizes oop,river,allin
set_bet_sizes ip,river,bet,30,50,75,125,200
set_bet_sizes ip,river,raise,75,175
set_bet_sizes ip,river,allin
set_allin_threshold 0.5
set_raise_limit 4
build_tree
set_thread_num {thread_num}
set_accuracy {accuracy}
set_max_iteration {max_iteration}
set_print_interval {print_interval}
set_use_isomorphism 1
set_enable_equity 1
set_enable_range 1
start_solve
set_dump_rounds 2
dump_result {output_file}
"""

# 默认 Range 配置
DEFAULT_RANGE_OOP = "AQs,AJs,ATs,A9s,A8s,A7s,A6s,A5s:0.75,A4s:0.75,A3s,A2s,AKo:0.25,KQs,KJs,KTs,K9s,K8s,K7s,K6s,K5s,K4s,K3s,K2s,AQo,KQo,QQ:0.25,QJs,QTs,Q9s,Q8s,Q7s,Q6s,Q5s,Q4s:0.75,Q3s:0.75,Q2s:0.75,AJo,KJo,QJo,JJ:0.75,JTs,J9s,J8s,J7s,J6s,J5s,J4s:0.75,J3s:0.75,J2s:0.75,ATo,KTo,QTo,JTo,TT:0.75,T9s,T8s,T7s:0.984,T6s:0.75,A9o,K9o,Q9o,J9o,T9o,99,98s,97s,96s:0.75,A8o:0.25,98o:0.25,88,87s,86s,85s:0.75,A7o:0.25,87o:0.25,77,76s,75s,74s:0.596,A6o:0.25,76o:0.25,66,65s,64s,A5o:0.25,65o:0.25,55,54s,53s,52s,A4o:0.25,54o:0.25,44:0.996,43s,42s,A3o:0.25,33,32s,A2o:0.25,22"
DEFAULT_RANGE_IP = "AA,AKs,AQs,AJs,ATs,A9s,A8s,A7s,A6s,A5s,A4s,A3s,A2s,AKo,KK,KQs,KJs,KTs,K9s,K8s:0.261,K7s:0.261,K6s:0.261,K5s:0.261,K4s:0.261,K3s:0.261,K2s:0.261,AQo,KQo,QQ,QJs,QTs,Q9s,AJo,KJo,QJo,JJ,JTs,J9s,ATo,KTo:0.261,TT,T9s,T8s:0.002,99,98s,88,87s,77,76s,66,65s,55,54s,44,33,22"
# =============================================


@dataclass
class SolveResult:
    """单次求解结果"""
    index: int
    board: str
    success: bool
    elapsed: float = 0.0
    error: str = ""
    retries: int = 0
    config_file: str = ""
    output_file: str = ""


@dataclass
class SolveStats:
    """求解统计信息"""
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    total_time: float = 0.0
    results: List[SolveResult] = field(default_factory=list)
    
    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.success / self.total * 100
    
    @property
    def avg_time(self) -> float:
        successful = [r.elapsed for r in self.results if r.success]
        if not successful:
            return 0.0
        return sum(successful) / len(successful)


def auto_compile_solver() -> bool:
    """自动编译 solver"""
    print("\n" + "=" * 60)
    print("检测到 console_solver 不存在，开始自动编译...")
    print("=" * 60)
    
    try:
        if IS_WINDOWS:
            compile_script = SCRIPT_DIR / "compile.ps1"
            if not compile_script.exists():
                print(f"[错误] 编译脚本不存在: {compile_script}")
                return False
            
            print(f"[编译] 执行: powershell -ExecutionPolicy Bypass -File {compile_script}")
            result = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(compile_script)],
                cwd=str(SCRIPT_DIR),
                capture_output=False
            )
        else:
            compile_script = SCRIPT_DIR / "compile.sh"
            if not compile_script.exists():
                print(f"[错误] 编译脚本不存在: {compile_script}")
                return False
            
            os.chmod(str(compile_script), 0o755)
            print(f"[编译] 执行: bash {compile_script}")
            result = subprocess.run(
                ["bash", str(compile_script)],
                cwd=str(SCRIPT_DIR),
                capture_output=False
            )
        
        if result.returncode == 0:
            print("\n" + "=" * 60)
            print("[成功] 编译完成!")
            print("=" * 60 + "\n")
            return True
        else:
            print(f"\n[错误] 编译失败，返回码: {result.returncode}")
            return False
            
    except Exception as e:
        print(f"[错误] 编译过程出错: {e}")
        return False


def ensure_solver_exists() -> bool:
    """确保 solver 可执行文件存在"""
    if os.path.exists(SOLVER_EXE):
        return True
    
    print(f"[警告] Solver 不存在: {SOLVER_EXE}")
    
    if auto_compile_solver():
        if os.path.exists(SOLVER_EXE):
            return True
        else:
            print(f"[错误] 编译后仍找不到 solver: {SOLVER_EXE}")
            return False
    
    return False


def read_cards_from_txt(txt_path: Path) -> List[Tuple[int, str]]:
    """
    从 txt 文件读取牌面列表
    
    Args:
        txt_path: txt 文件路径
        
    Returns:
        牌面列表，每项为 (行号, 牌面字符串)
    """
    if not txt_path.exists():
        raise FileNotFoundError(f"文件不存在: {txt_path}")
    
    boards = []
    with open(txt_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, start=1):
            board = line.strip()
            if board:
                # 标准化牌面格式：确保用逗号分隔
                board = normalize_board(board)
                boards.append((line_num, board))
    
    return boards


def read_cards_from_excel(excel_path: Path, board_column: str = "A") -> List[Tuple[int, str]]:
    """
    从 Excel 文件读取牌面列表
    
    Args:
        excel_path: Excel 文件路径
        board_column: 牌面所在列（默认 A 列）
        
    Returns:
        牌面列表，每项为 (行号, 牌面字符串)
    """
    try:
        import openpyxl
    except ImportError:
        raise ImportError("需要安装 openpyxl 库来读取 Excel 文件，请运行: pip install openpyxl")
    
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel 文件不存在: {excel_path}")
    
    wb = openpyxl.load_workbook(excel_path, read_only=True)
    ws = wb.active
    
    boards = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, values_only=True), start=1):
        # 获取指定列的值
        col_idx = ord(board_column.upper()) - ord('A')
        if col_idx < len(row) and row[col_idx]:
            board = str(row[col_idx]).strip()
            if board:
                # 标准化牌面格式：确保用逗号分隔
                board = normalize_board(board)
                boards.append((row_idx, board))
    
    wb.close()
    return boards


def read_cards(file_path: Path, board_column: str = "A") -> List[Tuple[int, str]]:
    """
    自动识别文件类型并读取牌面列表
    优先读取 txt 文件
    
    Args:
        file_path: 文件路径（支持 .txt 或 .xlsx）
        board_column: Excel 文件的牌面所在列（默认 A 列）
        
    Returns:
        牌面列表，每项为 (行号, 牌面字符串)
    """
    suffix = file_path.suffix.lower()
    
    if suffix == '.txt':
        return read_cards_from_txt(file_path)
    elif suffix == '.xlsx':
        return read_cards_from_excel(file_path, board_column)
    else:
        # 尝试作为文本文件读取
        return read_cards_from_txt(file_path)


def normalize_board(board: str) -> str:
    """
    标准化牌面格式
    将 "AcAdAh" 转换为 "Ac,Ad,Ah"
    """
    board = board.strip()
    
    # 如果已经有逗号分隔，直接返回
    if "," in board:
        return board
    
    # 否则每两个字符插入逗号
    cards = []
    for i in range(0, len(board), 2):
        if i + 2 <= len(board):
            cards.append(board[i:i+2])
    
    return ",".join(cards)


def board_to_filename(board: str) -> str:
    """
    将牌面转换为文件名
    "Ac,Ad,Ah" -> "AcAdAh"
    """
    return board.replace(",", "")


def generate_config_file(
    board: str,
    output_dir: Path,
    pot: int = 5,
    effective_stack: int = 100,
    thread_num: int = -1,
    accuracy: float = 1,
    max_iteration: int = 300,
    print_interval: int = 60,
    range_oop: str = None,
    range_ip: str = None
) -> Path:
    """
    生成配置文件
    
    Args:
        board: 牌面字符串（逗号分隔）
        output_dir: 配置文件输出目录
        其他参数: 求解器配置
        
    Returns:
        生成的配置文件路径
    """
    if range_oop is None:
        range_oop = DEFAULT_RANGE_OOP
    if range_ip is None:
        range_ip = DEFAULT_RANGE_IP
    
    filename = board_to_filename(board)
    config_path = output_dir / f"{filename}.txt"
    output_file = f"{filename}.json"
    
    config_content = CONFIG_TEMPLATE.format(
        pot=pot,
        effective_stack=effective_stack,
        board=board,
        range_oop=range_oop,
        range_ip=range_ip,
        thread_num=thread_num,
        accuracy=accuracy,
        max_iteration=max_iteration,
        print_interval=print_interval,
        output_file=output_file
    )
    
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(config_content)
    
    return config_path


def run_solver_with_retry(
    config_file: Path,
    max_retries: int = MAX_RETRIES,
    mode: str = "holdem"
) -> Tuple[bool, float, str, int]:
    """
    运行求解器，支持重试
    
    Args:
        config_file: 配置文件路径
        max_retries: 最大重试次数
        mode: 游戏模式
        
    Returns:
        (成功?, 耗时, 错误信息, 重试次数)
    """
    retries = 0
    last_error = ""
    
    while retries <= max_retries:
        if retries > 0:
            print(f"  [重试 {retries}/{max_retries}] 等待 5 秒后重试...")
            time.sleep(5)
        
        start_time = time.time()
        
        try:
            config_file_abs = str(config_file.resolve())
            cmd = [SOLVER_EXE, "-i", config_file_abs, "-r", RESOURCE_DIR, "-m", mode]
            
            # 确保结果目录存在
            RESULTS_DIR.mkdir(exist_ok=True)
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(RESULTS_DIR)
            )
            
            # 实时打印进度
            for line in process.stdout:
                print(line, end='')
            
            process.wait(timeout=TIMEOUT)
            elapsed = time.time() - start_time
            
            if process.returncode == 0:
                return True, elapsed, "", retries
            else:
                last_error = f"返回码: {process.returncode}"
                print(f"  [错误] {last_error}")
                
        except subprocess.TimeoutExpired:
            process.kill()
            last_error = f"求解超时 (>{TIMEOUT}秒)"
            print(f"  [错误] {last_error}")
        except Exception as e:
            last_error = str(e)
            print(f"  [错误] {last_error}")
        
        retries += 1
    
    return False, 0, last_error, retries - 1


def print_progress_bar(current: int, total: int, width: int = 40):
    """打印进度条"""
    percent = current / total
    filled = int(width * percent)
    bar = "█" * filled + "░" * (width - filled)
    print(f"\r进度: [{bar}] {current}/{total} ({percent*100:.1f}%)", end="", flush=True)


def print_summary(stats: SolveStats, start_time: datetime):
    """打印详细的汇总信息"""
    end_time = datetime.now()
    
    print("\n")
    print("=" * 70)
    print("                        求解完成 - 汇总报告")
    print("=" * 70)
    
    # 基本统计
    print(f"\n📊 基本统计:")
    print(f"   总任务数:     {stats.total}")
    print(f"   成功:         {stats.success} ✓")
    print(f"   失败:         {stats.failed} ✗")
    print(f"   跳过(超过重试): {stats.skipped}")
    print(f"   完成率:       {stats.success_rate:.1f}%")
    
    # 时间统计
    print(f"\n⏱️  时间统计:")
    print(f"   开始时间:     {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   结束时间:     {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   总耗时:       {stats.total_time:.1f} 秒 ({stats.total_time/60:.1f} 分钟)")
    if stats.success > 0:
        print(f"   平均耗时:     {stats.avg_time:.1f} 秒/任务")
        
        # 最快/最慢
        successful = [r for r in stats.results if r.success]
        if successful:
            fastest = min(successful, key=lambda x: x.elapsed)
            slowest = max(successful, key=lambda x: x.elapsed)
            print(f"   最快:         {fastest.board} ({fastest.elapsed:.1f}秒)")
            print(f"   最慢:         {slowest.board} ({slowest.elapsed:.1f}秒)")
    
    # 详细结果
    print(f"\n📋 详细结果:")
    print("-" * 70)
    print(f"{'序号':<6} {'牌面':<15} {'状态':<8} {'耗时':<12} {'重试':<6} {'备注'}")
    print("-" * 70)
    
    for result in stats.results:
        status = "✓ 成功" if result.success else "✗ 失败"
        elapsed_str = f"{result.elapsed:.1f}秒" if result.success else "-"
        note = result.error if result.error else ""
        print(f"{result.index:<6} {result.board:<15} {status:<8} {elapsed_str:<12} {result.retries:<6} {note}")
    
    print("-" * 70)
    
    # 失败详情
    failed = [r for r in stats.results if not r.success]
    if failed:
        print(f"\n❌ 失败详情:")
        for r in failed:
            print(f"   [{r.index}] {r.board}: {r.error}")
    
    print("\n" + "=" * 70)


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


def main():
    parser = argparse.ArgumentParser(
        description="TexasSolver 自动批量求解脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 求解第 1 到第 10 个牌面
  python auto_run_solver.py 1-10

  # 求解单个牌面
  python auto_run_solver.py 5

  # 混合范围和单个序号
  python auto_run_solver.py 1-10,15,20-30,35

  # 求解所有牌面
  python auto_run_solver.py all

  # 重新求解缺失的牌面（从 check_missing.py 输出复制）
  python auto_run_solver.py 427,430,433,436,439

  # 指定牌面文件
  python auto_run_solver.py 1-10 --file cards.txt

  # 自定义求解参数
  python auto_run_solver.py 1-10 --thread-num 8 --max-iteration 500
        """
    )
    
    # 范围参数（位置参数）
    parser.add_argument("range", nargs="?", help="序号范围（如: 1-10,15,20-30 或 all）")
    
    # 牌面文件配置
    parser.add_argument("--file", type=str, default="cards.txt", help="牌面文件名（默认: cards.txt，支持 .txt 或 .xlsx）")
    parser.add_argument("--column", type=str, default="A", help="Excel 文件的牌面所在列（默认: A）")
    
    # 求解器参数
    parser.add_argument("--pot", type=int, default=5, help="底池大小（默认: 5）")
    parser.add_argument("--stack", type=int, default=100, help="有效筹码（默认: 100）")
    parser.add_argument("--thread-num", type=int, default=-1, help="线程数（默认: -1，使用所有核心）")
    parser.add_argument("--accuracy", type=float, default=1, help="精度（默认: 1）")
    parser.add_argument("--max-iteration", type=int, default=300, help="最大迭代次数（默认: 300）")
    parser.add_argument("--print-interval", type=int, default=30, help="打印间隔（默认: 30）")
    parser.add_argument("--max-retries", type=int, default=3, help="最大重试次数（默认: 3）")
    
    args = parser.parse_args()
    
    # 参数检查
    if not args.range:
        parser.print_help()
        print("\n[错误] 请指定序号范围，如: 1-10 或 1-10,15,20-30 或 all")
        sys.exit(1)
    
    # 检查 solver
    if not ensure_solver_exists():
        print("[错误] Solver 不可用")
        sys.exit(1)
    
    # 牌面文件路径
    cards_path = CONFIG_DIR / args.file
    
    print("=" * 60)
    print("TexasSolver 自动批量求解")
    print("=" * 60)
    
    # 读取牌面列表
    try:
        print(f"\n[读取] 牌面文件: {cards_path}")
        all_boards = read_cards(cards_path, args.column)
        print(f"[读取] 共找到 {len(all_boards)} 个牌面")
    except Exception as e:
        print(f"[错误] 读取牌面文件失败: {e}")
        sys.exit(1)
    
    if not all_boards:
        print("[错误] 牌面文件中没有找到数据")
        sys.exit(1)
    
    # 解析范围表达式
    if args.range.lower() == "all":
        # 求解所有
        indices = list(range(1, len(all_boards) + 1))
        print(f"\n[任务] 将求解所有 {len(indices)} 个牌面")
    else:
        # 解析范围表达式
        indices = parse_range_expr(args.range, max_value=len(all_boards))
        
        if not indices:
            print("[错误] 没有有效的序号")
            sys.exit(1)
        
        # 显示解析结果
        if len(indices) <= 20:
            print(f"\n[任务] 将求解 {len(indices)} 个牌面: {indices}")
        else:
            print(f"\n[任务] 将求解 {len(indices)} 个牌面")
            print(f"[范围] {indices[0]}-{indices[-1]} (含 {len(indices)} 个序号)")
    
    # 筛选牌面
    boards_to_solve = [(i, all_boards[i - 1]) for i in indices]
    print(f"[配置] thread_num={args.thread_num}, max_iteration={args.max_iteration}")
    print(f"[容错] 最大重试次数: {args.max_retries}")
    
    # 显示牌面列表
    print(f"\n牌面列表:")
    for idx, (row_idx, board) in boards_to_solve:
        print(f"  [{idx}] {board}")
    
    print("\n" + "-" * 60)
    input("按 Enter 开始求解...")
    
    # 开始求解
    stats = SolveStats(total=len(boards_to_solve))
    start_time = datetime.now()
    total_start = time.time()
    interrupted = False
    
    try:
        for task_num, (idx, (row_idx, board)) in enumerate(boards_to_solve, 1):
            print(f"\n{'='*60}")
            print(f"[{task_num}/{len(boards_to_solve)}] 序号 {idx} - 求解牌面: {board}")
            print(f"{'='*60}")
            
            # 生成配置文件
            try:
                config_file = generate_config_file(
                    board=board,
                    output_dir=CONFIG_DIR,
                    pot=args.pot,
                    effective_stack=args.stack,
                    thread_num=args.thread_num,
                    accuracy=args.accuracy,
                    max_iteration=args.max_iteration,
                    print_interval=args.print_interval
                )
                print(f"[配置] 生成: {config_file.name}")
            except Exception as e:
                print(f"[错误] 生成配置文件失败: {e}")
                stats.failed += 1
                stats.results.append(SolveResult(
                    index=idx, board=board, success=False, error=f"配置文件生成失败: {e}"
                ))
                continue
            
            # 运行求解器
            success, elapsed, error, retries = run_solver_with_retry(
                config_file=config_file,
                max_retries=args.max_retries
            )
            
            result = SolveResult(
                index=idx,
                board=board,
                success=success,
                elapsed=elapsed,
                error=error,
                retries=retries,
                config_file=str(config_file),
                output_file=f"{board_to_filename(board)}.json"
            )
            stats.results.append(result)
            
            if success:
                stats.success += 1
                print(f"\n[完成] {board} - 耗时 {elapsed:.1f} 秒")
            else:
                if retries >= args.max_retries:
                    stats.skipped += 1
                    print(f"\n[跳过] {board} - 超过最大重试次数")
                else:
                    stats.failed += 1
                    print(f"\n[失败] {board} - {error}")
            
            # 打印进度
            print_progress_bar(task_num, len(boards_to_solve))
    
    except KeyboardInterrupt:
        interrupted = True
        print("\n\n" + "!" * 60)
        print("  用户中断 (Ctrl+C)")
        print("!" * 60)
    
    # 计算总时间
    stats.total_time = time.time() - total_start
    
    # 打印汇总（无论是否中断都打印）
    if interrupted:
        print("\n[提示] 以下是中断前已完成的任务统计:")
    print_summary(stats, start_time)
    
    # 如果被中断，输出未完成的任务
    if interrupted and len(stats.results) < len(boards_to_solve):
        completed_indices = {r.index for r in stats.results}
        remaining = [(idx, board) for idx, (row_idx, board) in boards_to_solve if idx not in completed_indices]
        
        if remaining:
            print(f"\n[未完成] 还有 {len(remaining)} 个任务未完成:")
            remaining_indices = [idx for idx, _ in remaining]
            # 生成紧凑的范围表达式
            resume_expr = compress_indices_to_expr(remaining_indices)
            print(f"   序号: {resume_expr}")
            print(f"\n[继续] 可以使用以下命令继续:")
            print(f"   python auto_run_solver.py {resume_expr}")


if __name__ == "__main__":
    main()
