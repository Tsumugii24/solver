"""
TexasSolver Console 求解脚本
直接读取 configs 目录下的配置文件进行求解
"""

import subprocess
import os
import sys
import time
import re
from pathlib import Path


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
# 注：C++ 端已内置精度控制，通常不需要后处理
POST_PROCESS = False
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


def format_json_floats(input_file: str, output_file: str = None, precision: int = 3):
    """
    使用正则表达式流式处理 JSON 文件，将浮点数格式化为指定小数位数。
    避免将整个文件加载到内存中。
    
    Args:
        input_file: 输入 JSON 文件路径
        output_file: 输出文件路径，默认覆盖原文件
        precision: 保留小数位数
    """
    if output_file is None:
        output_file = input_file
    
    # 匹配纯浮点数的正则表达式（不在引号内）
    float_pattern = re.compile(r'(?<!["\w])(-?\d+\.\d{4,})(?!["\w])')
    
    # 匹配 action 字符串中的数值，如 "BET 2.000000" -> "BET 2.00"
    action_pattern = re.compile(r'"(BET|RAISE|CALL|CHECK|FOLD|ALLIN)(\s+)(\d+\.\d+)"')
    
    temp_file = input_file + ".tmp"
    
    try:
        with open(input_file, 'r', encoding='utf-8') as fin, \
             open(temp_file, 'w', encoding='utf-8') as fout:
            
            # 逐块读取处理，每次读取 1MB
            chunk_size = 1024 * 1024
            buffer = ""
            
            while True:
                chunk = fin.read(chunk_size)
                if not chunk:
                    # 处理剩余 buffer
                    if buffer:
                        # 1. 处理 action 字符串中的数值
                        result = action_pattern.sub(
                            lambda m: f'"{m.group(1)}{m.group(2)}{float(m.group(3)):.2f}"',
                            buffer
                        )
                        # 2. 处理纯浮点数
                        result = float_pattern.sub(
                            lambda m: f"{float(m.group(1)):.{precision}f}", 
                            result
                        )
                        fout.write(result)
                    break
                
                buffer += chunk
                
                # 找到最后一个完整的数字边界（逗号、括号等）
                last_safe = max(
                    buffer.rfind(','),
                    buffer.rfind(']'),
                    buffer.rfind('}'),
                    buffer.rfind(':')
                )
                
                if last_safe > 0:
                    # 处理到安全位置
                    to_process = buffer[:last_safe + 1]
                    buffer = buffer[last_safe + 1:]
                    
                    # 1. 处理 action 字符串中的数值（保留2位小数）
                    result = action_pattern.sub(
                        lambda m: f'"{m.group(1)}{m.group(2)}{float(m.group(3)):.2f}"',
                        to_process
                    )
                    
                    # 2. 处理纯浮点数（保留指定位数小数）
                    result = float_pattern.sub(
                        lambda m: f"{float(m.group(1)):.{precision}f}", 
                        result
                    )
                    fout.write(result)
        
        # 替换原文件
        if os.path.exists(output_file) and output_file != temp_file:
            os.remove(output_file)
        os.rename(temp_file, output_file)
        
    except Exception as e:
        # 清理临时文件
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise e


def run_solver(config_file: str, mode: str = "holdem", post_process: bool = None) -> dict:
    """
    运行单个配置文件
    
    Args:
        config_file: 配置文件路径
        mode: 游戏模式 (holdem 或 shortdeck)
        post_process: 是否进行后处理（格式化浮点数），默认使用全局配置 POST_PROCESS
        
    Returns:
        运行结果字典
    """
    if post_process is None:
        post_process = POST_PROCESS
    if not os.path.exists(config_file):
        return {"success": False, "error": f"配置文件不存在: {config_file}"}
    
    # 检查求解器（如果不存在则自动编译）
    if not ensure_solver_exists():
        return {"success": False, "error": f"求解器不可用: {SOLVER_EXE}"}
    
    # 构建命令
    cmd = [SOLVER_EXE, "-i", config_file, "-r", RESOURCE_DIR, "-m", mode]
    
    print(f"\n{'='*60}")
    print(f"运行配置: {config_file}")
    print(f"命令: {' '.join(cmd)}")
    print(f"{'='*60}")
    
    start_time = time.time()
    
    try:
        # 使用绝对路径
        config_file_abs = os.path.abspath(config_file)
        cmd = [SOLVER_EXE, "-i", config_file_abs, "-r", RESOURCE_DIR, "-m", mode]
        
        # 创建结果目录
        Path(RESULTS_DIR).mkdir(exist_ok=True)
        
        # 运行求解器（工作目录设为results，结果文件会保存在那里）
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=RESULTS_DIR
        )
        
        # 实时打印进度（不存储，CFR结果由求解器直接写入JSON文件）
        for line in process.stdout:
            print(line, end='')
        
        process.wait(timeout=TIMEOUT)
        elapsed = time.time() - start_time
        
        if process.returncode == 0:
            print(f"\n[完成] 耗时: {elapsed:.1f}秒")
            
            # 后处理：格式化当前生成的 JSON 文件中的浮点数
            if post_process:
                # 从配置文件中解析 dump_result 的输出文件名
                output_files = []
                with open(config_file_abs, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('dump_result '):
                            output_files.append(line.split(None, 1)[1])
                
                for output_file in output_files:
                    json_file = Path(RESULTS_DIR) / output_file
                    if json_file.exists():
                        print(f"[后处理] 格式化浮点数: {json_file.name}")
                        try:
                            format_json_floats(str(json_file), precision=FLOAT_PRECISION)
                        except Exception as e:
                            print(f"  警告: 格式化失败 - {e}")
            
            return {
                "success": True,
                "elapsed": elapsed,
                "config": config_file
            }
        else:
            print(f"\n[错误] 返回码: {process.returncode}")
            return {
                "success": False,
                "error": f"返回码: {process.returncode}",
                "config": config_file
            }
            
    except subprocess.TimeoutExpired:
        process.kill()
        return {"success": False, "error": f"求解超时 (>{TIMEOUT}秒)", "config": config_file}
    except Exception as e:
        return {"success": False, "error": str(e), "config": config_file}


def run_all_configs(pattern: str = "*.txt", post_process: bool = None):
    """
    运行 configs 目录下所有匹配的配置文件
    
    Args:
        pattern: 文件匹配模式，默认 *.txt
        post_process: 是否进行后处理，默认使用全局配置
    """
    if post_process is None:
        post_process = POST_PROCESS
    # 检查求解器（如果不存在则自动编译）
    if not ensure_solver_exists():
        print(f"[错误] 求解器不可用: {SOLVER_EXE}")
        return
    
    if not os.path.exists(RESOURCE_DIR):
        print(f"[错误] Resources目录不存在: {RESOURCE_DIR}")
        print("请修改脚本顶部的 RESOURCE_DIR 路径")
        return
    
    # 获取配置文件
    config_path = Path(CONFIG_DIR)
    if not config_path.exists():
        print(f"[错误] 配置目录不存在: {CONFIG_DIR}")
        return
    
    config_files = sorted(config_path.glob(pattern))
    
    if not config_files:
        print(f"未找到配置文件: {CONFIG_DIR}/{pattern}")
        return
    
    # 创建结果目录
    Path(RESULTS_DIR).mkdir(exist_ok=True)
    
    print(f"\n找到 {len(config_files)} 个配置文件")
    print("="*60)
    
    results = []
    start_total = time.time()
    
    for i, config_file in enumerate(config_files, 1):
        print(f"\n[{i}/{len(config_files)}] 处理: {config_file.name}")
        
        result = run_solver(str(config_file), post_process=post_process)
        results.append(result)
        
        # 移动结果文件到 results 目录
        if result["success"]:
            # 查找可能的输出文件
            config_dir = config_file.parent
            for json_file in config_dir.glob("*.json"):
                dst = Path(RESULTS_DIR) / json_file.name
                try:
                    json_file.rename(dst)
                    print(f"  结果保存到: {dst}")
                except:
                    pass
    
    # 汇总
    elapsed_total = time.time() - start_total
    success_count = sum(1 for r in results if r["success"])
    
    print("\n" + "="*60)
    print(f"批量运行完成!")
    print(f"成功: {success_count}/{len(config_files)}")
    print(f"总耗时: {elapsed_total/60:.1f} 分钟")
    print("="*60)


def main():
    """主函数"""
    print("="*60)
    print("TexasSolver Console Runner")
    print("="*60)
    
    # 解析 --no-post-process 选项
    args = sys.argv[1:]
    post_process = None  # 使用默认值
    if "--no-post-process" in args:
        post_process = False
        args.remove("--no-post-process")
    elif "--post-process" in args:
        post_process = True
        args.remove("--post-process")
    
    if len(args) < 1:
        # 显示帮助
        print("\n用法:")
        print(f"  python {sys.argv[0]} <配置文件>      - 运行单个配置文件")
        print(f"  python {sys.argv[0]} all            - 运行 configs/*.txt 所有配置")
        print(f"  python {sys.argv[0]} all config_*.txt  - 运行匹配模式的配置")
        print()
        print("选项:")
        print("  --no-post-process  禁用后处理（不格式化JSON浮点数）")
        print("  --post-process     启用后处理（默认）")
        print()
        print("示例:")
        print(f"  python {sys.argv[0]} configs/sia_sod_template.txt")
        print(f"  python {sys.argv[0]} configs/sia_sod_template.txt --no-post-process")
        print(f"  python {sys.argv[0]} all")
        print(f"  python {sys.argv[0]} all sia_*.txt")
        return
    
    arg = args[0]
    
    if arg == "all":
        # 批量运行
        pattern = args[1] if len(args) > 1 else "*.txt"
        run_all_configs(pattern, post_process=post_process)
    else:
        # 运行单个文件
        result = run_solver(arg, post_process=post_process)
        if not result["success"]:
            print(f"\n[失败] {result.get('error', '未知错误')}")
            sys.exit(1)


if __name__ == "__main__":
    main()
