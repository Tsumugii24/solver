"""
TexasSolver Console 并行求解脚本
支持多进程同时读取多个配置文件并行求解
"""

import subprocess
import os
import sys
import time
import re
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Optional
import multiprocessing
from multiprocessing import Manager


# 全局打印锁（用于进程间同步打印）
_print_lock = None


def safe_print(*args, **kwargs):
    """
    进程安全的打印函数
    使用锁确保多进程打印不会交错
    """
    global _print_lock
    if _print_lock is not None:
        with _print_lock:
            print(*args, **kwargs)
            sys.stdout.flush()
    else:
        print(*args, **kwargs)
        sys.stdout.flush()


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
CONFIG_DIR = "configs"
# 结果输出目录
RESULTS_DIR = "results"
# 超时时间（秒）
TIMEOUT = 7200  # 2小时
# 浮点数保留小数位数
FLOAT_PRECISION = 3
# 是否启用后处理（格式化JSON中的浮点数）
POST_PROCESS = False
# 系统 CPU 线程数
CPU_COUNT = multiprocessing.cpu_count()
# =============================================


def auto_compile_solver() -> bool:
    """
    自动编译 solver
    根据操作系统自动选择编译脚本
    
    Returns:
        编译是否成功
    """
    print("\n" + "=" * 60)
    print("检测到 console_solver 不存在，开始自动编译...")
    print("=" * 60)
    
    try:
        if IS_WINDOWS:
            # Windows: 使用 PowerShell 执行 compile.ps1
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
            # Linux/macOS: 使用 bash 执行 compile.sh
            compile_script = SCRIPT_DIR / "compile.sh"
            if not compile_script.exists():
                print(f"[错误] 编译脚本不存在: {compile_script}")
                return False
            
            # 确保脚本有执行权限
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
            
    except FileNotFoundError as e:
        print(f"[错误] 找不到编译工具: {e}")
        print("请确保已安装以下依赖:")
        if IS_WINDOWS:
            print("  - CMake")
            print("  - Ninja")
            print("  - MinGW-w64 或 MSVC")
        else:
            print("  - CMake")
            print("  - make")
            print("  - g++")
        return False
    except Exception as e:
        print(f"[错误] 编译过程出错: {e}")
        return False


def ensure_solver_exists() -> bool:
    """
    确保 solver 可执行文件存在
    如果不存在则自动编译
    
    Returns:
        solver 是否可用
    """
    if os.path.exists(SOLVER_EXE):
        return True
    
    print(f"[警告] Solver 不存在: {SOLVER_EXE}")
    
    # 尝试自动编译
    if auto_compile_solver():
        # 再次检查
        if os.path.exists(SOLVER_EXE):
            return True
        else:
            print(f"[错误] 编译后仍找不到 solver: {SOLVER_EXE}")
            return False
    
    return False


def parse_thread_num_from_config(config_file: str) -> Optional[int]:
    """
    从配置文件中解析 set_thread_num 的值
    
    Args:
        config_file: 配置文件路径
        
    Returns:
        thread_num 值，如果未找到或为 -1 则返回 None
    """
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('set_thread_num '):
                    parts = line.split()
                    if len(parts) >= 2:
                        thread_num = int(parts[1])
                        # -1 表示使用所有核心
                        if thread_num == -1:
                            return None
                        return thread_num
    except Exception:
        pass
    return None


def calculate_optimal_workers(config_files: List[str]) -> int:
    """
    根据配置文件中的 set_thread_num 计算最优的 workers 数量
    
    公式: workers = CPU线程数 / set_thread_num
    
    Args:
        config_files: 配置文件路径列表
        
    Returns:
        计算得到的最优 workers 数量
    """
    thread_nums = []
    
    for config_file in config_files:
        thread_num = parse_thread_num_from_config(config_file)
        if thread_num is not None:
            thread_nums.append(thread_num)
    
    if not thread_nums:
        # 没有找到有效的 thread_num，使用默认值（CPU核心数的一半）
        return max(1, CPU_COUNT // 2)
    
    # 检查是否所有配置文件的 thread_num 一致
    unique_thread_nums = set(thread_nums)
    if len(unique_thread_nums) > 1:
        print(f"[警告] 配置文件中的 set_thread_num 不一致: {unique_thread_nums}")
        print(f"[警告] 将使用最大值 {max(thread_nums)} 来计算 workers 数量")
    
    # 使用最大的 thread_num 来计算（保守策略，避免资源竞争）
    max_thread_num = max(thread_nums)
    optimal_workers = max(1, CPU_COUNT // max_thread_num)
    
    return optimal_workers


def format_json_floats(input_file: str, output_file: str = None, precision: int = 3):
    """
    使用正则表达式流式处理 JSON 文件，将浮点数格式化为指定小数位数。
    """
    if output_file is None:
        output_file = input_file
    
    float_pattern = re.compile(r'(?<!["\w])(-?\d+\.\d{4,})(?!["\w])')
    action_pattern = re.compile(r'"(BET|RAISE|CALL|CHECK|FOLD|ALLIN)(\s+)(\d+\.\d+)"')
    
    temp_file = input_file + ".tmp"
    
    try:
        with open(input_file, 'r', encoding='utf-8') as fin, \
             open(temp_file, 'w', encoding='utf-8') as fout:
            
            chunk_size = 1024 * 1024
            buffer = ""
            
            while True:
                chunk = fin.read(chunk_size)
                if not chunk:
                    if buffer:
                        result = action_pattern.sub(
                            lambda m: f'"{m.group(1)}{m.group(2)}{float(m.group(3)):.2f}"',
                            buffer
                        )
                        result = float_pattern.sub(
                            lambda m: f"{float(m.group(1)):.{precision}f}", 
                            result
                        )
                        fout.write(result)
                    break
                
                buffer += chunk
                last_safe = max(
                    buffer.rfind(','),
                    buffer.rfind(']'),
                    buffer.rfind('}'),
                    buffer.rfind(':')
                )
                
                if last_safe > 0:
                    to_process = buffer[:last_safe + 1]
                    buffer = buffer[last_safe + 1:]
                    
                    result = action_pattern.sub(
                        lambda m: f'"{m.group(1)}{m.group(2)}{float(m.group(3)):.2f}"',
                        to_process
                    )
                    result = float_pattern.sub(
                        lambda m: f"{float(m.group(1)):.{precision}f}", 
                        result
                    )
                    fout.write(result)
        
        if os.path.exists(output_file) and output_file != temp_file:
            os.remove(output_file)
        os.rename(temp_file, output_file)
        
    except Exception as e:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise e


def init_worker(lock):
    """
    初始化 worker 进程，设置共享的打印锁
    """
    global _print_lock
    _print_lock = lock


def run_solver_worker(args: tuple) -> dict:
    """
    工作进程函数：运行单个配置文件的求解器
    
    Args:
        args: (config_file, mode, post_process, worker_id) 的元组
        
    Returns:
        运行结果字典
    """
    config_file, mode, post_process, worker_id = args
    
    if not os.path.exists(config_file):
        return {"success": False, "error": f"配置文件不存在: {config_file}", "config": config_file, "worker_id": worker_id}
    
    if not os.path.exists(SOLVER_EXE):
        return {"success": False, "error": f"求解器不存在: {SOLVER_EXE}", "config": config_file, "worker_id": worker_id}
    
    config_file_abs = os.path.abspath(config_file)
    config_name = Path(config_file).stem
    
    # 构建命令
    cmd = [SOLVER_EXE, "-i", config_file_abs, "-r", RESOURCE_DIR, "-m", mode]
    
    safe_print(f"[Worker-{worker_id}] 开始求解: {config_name}")
    
    start_time = time.time()
    
    try:
        # 创建结果目录
        Path(RESULTS_DIR).mkdir(exist_ok=True)
        
        # 运行求解器，将输出重定向到日志文件
        log_file = Path(RESULTS_DIR) / f"{config_name}_worker{worker_id}.log"
        
        with open(log_file, 'w', encoding='utf-8') as log:
            log.write(f"命令: {' '.join(cmd)}\n")
            log.write(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            log.write("=" * 60 + "\n\n")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=RESULTS_DIR
            )
            
            # 读取并写入日志，同时打印进度信息
            last_progress_time = time.time()
            for line in process.stdout:
                log.write(line)
                log.flush()
                # 每30秒打印一次进度提示
                if time.time() - last_progress_time > 30:
                    elapsed = time.time() - start_time
                    safe_print(f"[Worker-{worker_id}] {config_name} 运行中... ({elapsed:.0f}秒)")
                    last_progress_time = time.time()
            
            process.wait(timeout=TIMEOUT)
            
            elapsed = time.time() - start_time
            log.write(f"\n{'=' * 60}\n")
            log.write(f"结束时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            log.write(f"耗时: {elapsed:.1f}秒\n")
            log.write(f"返回码: {process.returncode}\n")
        
        if process.returncode == 0:
            safe_print(f"[Worker-{worker_id}] 完成: {config_name} (耗时 {elapsed:.1f}秒)")
            
            # 后处理
            if post_process:
                output_files = []
                with open(config_file_abs, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('dump_result '):
                            output_files.append(line.split(None, 1)[1])
                
                for output_file in output_files:
                    json_file = Path(RESULTS_DIR) / output_file
                    if json_file.exists():
                        try:
                            format_json_floats(str(json_file), precision=FLOAT_PRECISION)
                        except Exception as e:
                            safe_print(f"[Worker-{worker_id}] 警告: 格式化失败 {json_file.name} - {e}")
            
            return {
                "success": True,
                "elapsed": elapsed,
                "config": config_file,
                "worker_id": worker_id,
                "log_file": str(log_file)
            }
        else:
            safe_print(f"[Worker-{worker_id}] 错误: {config_name} (返回码 {process.returncode})")
            return {
                "success": False,
                "error": f"返回码: {process.returncode}",
                "config": config_file,
                "worker_id": worker_id,
                "log_file": str(log_file)
            }
            
    except subprocess.TimeoutExpired:
        process.kill()
        safe_print(f"[Worker-{worker_id}] 超时: {config_name}")
        return {"success": False, "error": f"求解超时 (>{TIMEOUT}秒)", "config": config_file, "worker_id": worker_id}
    except Exception as e:
        safe_print(f"[Worker-{worker_id}] 异常: {config_name} - {e}")
        return {"success": False, "error": str(e), "config": config_file, "worker_id": worker_id}


def run_parallel(config_files: List[str], workers: int = None, mode: str = "holdem", 
                 post_process: bool = None, auto_workers: bool = True) -> List[dict]:
    """
    并行运行多个配置文件
    
    Args:
        config_files: 配置文件路径列表
        workers: 并行进程数，如果为 None 则自动计算
        mode: 游戏模式 (holdem 或 shortdeck)
        post_process: 是否进行后处理
        auto_workers: 是否自动根据 set_thread_num 计算 workers 数量
        
    Returns:
        所有运行结果的列表
    """
    if post_process is None:
        post_process = POST_PROCESS
    
    # 检查求解器（如果不存在则自动编译）
    if not ensure_solver_exists():
        print(f"[错误] 求解器不可用: {SOLVER_EXE}")
        return []
    
    if not os.path.exists(RESOURCE_DIR):
        print(f"[错误] Resources目录不存在: {RESOURCE_DIR}")
        return []
    
    # 创建结果目录
    Path(RESULTS_DIR).mkdir(exist_ok=True)
    
    # 过滤有效的配置文件
    valid_configs = []
    for cf in config_files:
        if os.path.exists(cf):
            valid_configs.append(cf)
        else:
            print(f"[警告] 跳过不存在的文件: {cf}")
    
    if not valid_configs:
        print("[错误] 没有有效的配置文件")
        return []
    
    # 计算 workers 数量
    if workers is None and auto_workers:
        # 自动根据配置文件中的 set_thread_num 计算
        workers = calculate_optimal_workers(valid_configs)
        print(f"\n[自动计算] CPU线程数: {CPU_COUNT}, 根据配置文件自动设置 workers: {workers}")
    elif workers is None:
        workers = max(1, CPU_COUNT // 2)
    
    # 调整 worker 数量（不超过配置文件数量）
    actual_workers = min(workers, len(valid_configs))
    
    # 获取配置文件的 thread_num 信息
    config_thread_nums = {}
    for config in valid_configs:
        thread_num = parse_thread_num_from_config(config)
        config_thread_nums[config] = thread_num if thread_num else CPU_COUNT
    
    print("\n" + "=" * 60)
    print("TexasSolver 并行求解")
    print("=" * 60)
    print(f"系统CPU线程数: {CPU_COUNT}")
    print(f"配置文件数: {len(valid_configs)}")
    print(f"并行进程数: {actual_workers}")
    print(f"游戏模式: {mode}")
    print(f"后处理: {'启用' if post_process else '禁用'}")
    print("=" * 60)
    
    # 准备任务参数
    tasks = [
        (config, mode, post_process, i) 
        for i, config in enumerate(valid_configs, 1)
    ]
    
    print(f"\n配置文件列表:")
    for i, config in enumerate(valid_configs, 1):
        thread_num = config_thread_nums[config]
        print(f"  [{i}] {Path(config).name} (thread_num={thread_num})")
    print()
    
    start_total = time.time()
    results = []
    
    # 创建共享的打印锁
    manager = Manager()
    print_lock = manager.Lock()
    
    # 使用进程池并行执行
    with ProcessPoolExecutor(
        max_workers=actual_workers,
        initializer=init_worker,
        initargs=(print_lock,)
    ) as executor:
        # 提交所有任务
        future_to_config = {
            executor.submit(run_solver_worker, task): task[0] 
            for task in tasks
        }
        
        # 收集结果
        for future in as_completed(future_to_config):
            config = future_to_config[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"[错误] {Path(config).name}: {e}")
                results.append({
                    "success": False,
                    "error": str(e),
                    "config": config
                })
    
    # 汇总统计
    elapsed_total = time.time() - start_total
    success_count = sum(1 for r in results if r.get("success", False))
    
    print("\n" + "=" * 60)
    print("并行求解完成!")
    print("=" * 60)
    print(f"成功: {success_count}/{len(valid_configs)}")
    print(f"总耗时: {elapsed_total:.1f}秒 ({elapsed_total/60:.1f}分钟)")
    
    if success_count > 0:
        successful_times = [r["elapsed"] for r in results if r.get("success", False)]
        print(f"平均单任务耗时: {sum(successful_times)/len(successful_times):.1f}秒")
    
    # 显示详细结果
    print("\n详细结果:")
    for result in sorted(results, key=lambda x: x.get("worker_id", 0)):
        config_name = Path(result.get("config", "unknown")).name
        if result.get("success", False):
            print(f"  [成功] {config_name} - {result['elapsed']:.1f}秒")
        else:
            print(f"  [失败] {config_name} - {result.get('error', '未知错误')}")
    
    print("=" * 60)
    
    return results


def get_config_files(patterns: List[str]) -> List[str]:
    """
    根据模式获取配置文件列表
    
    Args:
        patterns: 文件路径或通配符模式列表
        
    Returns:
        匹配的配置文件路径列表
    """
    config_files = []
    
    for pattern in patterns:
        path = Path(pattern)
        
        # 如果是具体文件
        if path.exists() and path.is_file():
            config_files.append(str(path))
        # 如果包含通配符
        elif '*' in pattern or '?' in pattern:
            # 在 configs 目录下查找
            if not pattern.startswith(CONFIG_DIR):
                pattern = str(Path(CONFIG_DIR) / pattern)
            matches = list(Path('.').glob(pattern))
            config_files.extend(str(m) for m in matches)
        # 尝试在 configs 目录下查找
        else:
            config_path = Path(CONFIG_DIR) / pattern
            if config_path.exists():
                config_files.append(str(config_path))
    
    # 去重并排序
    return sorted(set(config_files))


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="TexasSolver Console 并行求解脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
示例:
  # 并行运行（自动根据 set_thread_num 计算 workers 数量）
  python run_solver_parallel.py configs/*.txt

  # 手动指定 4 个进程并行
  python run_solver_parallel.py -w 4 configs/*.txt

  # 运行所有 configs 目录下的配置
  python run_solver_parallel.py all

  # 运行匹配模式的配置
  python run_solver_parallel.py all "*_example.txt"

自动计算 workers 逻辑:
  workers = CPU线程数({CPU_COUNT}) / set_thread_num
  例如: set_thread_num=16, 则 workers = {CPU_COUNT} / 16 = {CPU_COUNT // 16}
        """
    )
    
    parser.add_argument(
        'configs', 
        nargs='+',
        help='配置文件路径或 "all" 运行所有配置'
    )
    parser.add_argument(
        '-w', '--workers',
        type=int,
        default=None,
        help='并行进程数 (默认: 自动计算 = CPU线程数 / set_thread_num)'
    )
    parser.add_argument(
        '-m', '--mode',
        choices=['holdem', 'shortdeck'],
        default='holdem',
        help='游戏模式 (默认: holdem)'
    )
    parser.add_argument(
        '--post-process',
        action='store_true',
        help='启用后处理（格式化JSON浮点数）'
    )
    parser.add_argument(
        '--no-post-process',
        action='store_true',
        help='禁用后处理'
    )
    
    args = parser.parse_args()
    
    # 处理后处理选项
    post_process = None
    if args.post_process:
        post_process = True
    elif args.no_post_process:
        post_process = False
    
    # 获取配置文件列表
    if args.configs[0] == 'all':
        # 运行所有配置
        pattern = args.configs[1] if len(args.configs) > 1 else "*.txt"
        config_path = Path(CONFIG_DIR)
        if not config_path.exists():
            print(f"[错误] 配置目录不存在: {CONFIG_DIR}")
            sys.exit(1)
        config_files = sorted(str(f) for f in config_path.glob(pattern))
    else:
        config_files = get_config_files(args.configs)
    
    if not config_files:
        print("[错误] 未找到任何配置文件")
        sys.exit(1)
    
    # 运行并行求解
    results = run_parallel(
        config_files,
        workers=args.workers,
        mode=args.mode,
        post_process=post_process
    )
    
    # 根据结果设置退出码
    if not results or not all(r.get("success", False) for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
