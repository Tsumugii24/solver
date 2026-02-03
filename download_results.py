"""
下载远程 ZIP 文件到本地目录
支持 Jupyter Server 的文件链接
支持断点续传和自动重试
"""

import argparse
import os
import sys
import time
import zipfile
import warnings
from pathlib import Path
from urllib.parse import urlparse, unquote

# 禁用 SSL 警告
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

try:
    import requests
except ImportError:
    print("[错误] 需要安装 requests 库")
    print("请运行: pip install requests")
    sys.exit(1)


# ==================== 配置 ====================
DEFAULT_OUTPUT_DIR = Path("D:/results")
CHUNK_SIZE = 1024 * 1024  # 1MB（大文件用更大的块）
MAX_RETRIES = 1000  # 最大重试次数
RETRY_DELAY = 1  # 重试间隔（秒）
TIMEOUT = 60  # 连接超时（秒）
# =============================================


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


def format_time(seconds: float) -> str:
    """格式化时间"""
    if seconds < 60:
        return f"{seconds:.0f}秒"
    elif seconds < 3600:
        return f"{seconds/60:.1f}分钟"
    else:
        return f"{seconds/3600:.1f}小时"


def download_file_with_resume(
    url: str,
    output_dir: Path,
    filename: str = None,
    token: str = None,
    cookie: str = None,
    extract: bool = True,
    verify_ssl: bool = True,
    max_retries: int = MAX_RETRIES
) -> bool:
    """
    下载文件（支持断点续传和自动重试）
    
    Args:
        url: 下载链接
        output_dir: 输出目录
        filename: 保存的文件名（默认从URL或响应头获取）
        token: Jupyter token（如果需要认证）
        cookie: Cookie 字符串（如果需要认证）
        extract: 是否自动解压 ZIP 文件
        verify_ssl: 是否验证 SSL 证书
        max_retries: 最大重试次数
        
    Returns:
        是否成功
    """
    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 构建请求头
    base_headers = {}
    if token:
        base_headers['Authorization'] = f'token {token}'
    if cookie:
        base_headers['Cookie'] = cookie
    
    print(f"\n[下载] URL: {url[:100]}...")
    print(f"[目标] 目录: {output_dir}")
    
    # 首先获取文件信息
    try:
        head_response = requests.head(
            url,
            headers=base_headers,
            verify=verify_ssl,
            timeout=TIMEOUT,
            allow_redirects=True
        )
    except Exception:
        # HEAD 请求失败，尝试 GET
        head_response = None
    
    # 确定文件名
    if filename is None:
        if head_response and head_response.headers.get('Content-Disposition'):
            cd = head_response.headers.get('Content-Disposition')
            if 'filename=' in cd:
                filename = cd.split('filename=')[-1].strip('"\'')
        
        if not filename:
            parsed = urlparse(url)
            filename = unquote(Path(parsed.path).name)
            if not filename or filename == '/':
                filename = 'download.zip'
    
    output_path = output_dir / filename
    temp_path = output_dir / f"{filename}.downloading"
    
    # 获取文件总大小
    total_size = 0
    if head_response:
        total_size = int(head_response.headers.get('content-length', 0))
    
    # 检查服务器是否支持断点续传
    supports_resume = False
    if head_response:
        accept_ranges = head_response.headers.get('Accept-Ranges', '')
        supports_resume = accept_ranges.lower() == 'bytes'
    
    print(f"[文件] 名称: {filename}")
    if total_size > 0:
        print(f"[大小] {format_size(total_size)}")
    print(f"[续传] {'支持' if supports_resume else '不支持'}")
    
    # 检查是否有未完成的下载
    downloaded = 0
    if temp_path.exists() and supports_resume:
        downloaded = temp_path.stat().st_size
        print(f"[续传] 发现未完成的下载，已下载: {format_size(downloaded)}")
    
    # 开始下载（支持重试）
    start_time = time.time()
    retry_count = 0
    
    while retry_count <= max_retries:
        try:
            # 构建请求头（包含断点续传）
            headers = base_headers.copy()
            
            # 如果支持断点续传且已有部分数据
            if supports_resume and downloaded > 0:
                headers['Range'] = f'bytes={downloaded}-'
                print(f"[续传] 从 {format_size(downloaded)} 处继续下载...")
            
            # 发起请求
            response = requests.get(
                url,
                headers=headers,
                stream=True,
                verify=verify_ssl,
                timeout=TIMEOUT
            )
            
            # 检查响应状态
            if response.status_code == 401:
                print("[错误] 需要认证，请提供 --token 参数")
                return False
            elif response.status_code == 403:
                print("[错误] 访问被拒绝")
                return False
            elif response.status_code == 404:
                print("[错误] 文件不存在")
                return False
            elif response.status_code == 416:
                # Range 请求超出范围，说明文件已下载完成
                print("[信息] 文件已下载完成")
                if temp_path.exists():
                    temp_path.rename(output_path)
                break
            elif response.status_code not in [200, 206]:
                print(f"[错误] HTTP 状态码: {response.status_code}")
                return False
            
            # 如果是新下载，更新总大小
            if response.status_code == 200:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0  # 重新开始
            elif response.status_code == 206:
                # 部分内容
                content_range = response.headers.get('Content-Range', '')
                if content_range and '/' in content_range:
                    total_size = int(content_range.split('/')[-1])
            
            # 打开文件（追加或新建）
            mode = 'ab' if (supports_resume and downloaded > 0) else 'wb'
            
            with open(temp_path, mode) as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # 显示进度
                        if total_size > 0:
                            percent = downloaded / total_size * 100
                            bar_len = 40
                            filled = int(bar_len * downloaded / total_size)
                            bar = '█' * filled + '░' * (bar_len - filled)
                            
                            elapsed = time.time() - start_time
                            speed = downloaded / elapsed if elapsed > 0 else 0
                            eta = (total_size - downloaded) / speed if speed > 0 else 0
                            
                            print(f"\r[进度] [{bar}] {percent:.1f}% ({format_size(downloaded)}/{format_size(total_size)}) "
                                  f"速度: {format_size(speed)}/s 剩余: {format_time(eta)}   ", end='', flush=True)
                        else:
                            print(f"\r[进度] 已下载: {format_size(downloaded)}", end='', flush=True)
            
            # 下载完成，重命名文件
            if total_size == 0 or downloaded >= total_size:
                if temp_path.exists():
                    if output_path.exists():
                        output_path.unlink()
                    temp_path.rename(output_path)
                
                elapsed = time.time() - start_time
                speed = downloaded / elapsed if elapsed > 0 else 0
                
                print(f"\n[完成] 下载完成!")
                print(f"[耗时] {format_time(elapsed)} (平均速度: {format_size(speed)}/s)")
                print(f"[保存] {output_path}")
                
                # 解压 ZIP 文件
                if extract and filename.lower().endswith('.zip'):
                    print(f"\n[解压] 正在解压...")
                    try:
                        with zipfile.ZipFile(output_path, 'r') as zf:
                            file_list = zf.namelist()
                            print(f"[解压] 共 {len(file_list)} 个文件")
                            
                            # 检查是否所有文件都在同一个根目录下
                            # 如果是，则去掉这个根目录前缀，直接解压到 output_dir
                            root_dirs = set()
                            for name in file_list:
                                parts = name.split('/')
                                if len(parts) > 1 and parts[0]:
                                    root_dirs.add(parts[0])
                            
                            # 如果所有文件都在同一个根目录下（如 results/）
                            if len(root_dirs) == 1:
                                root_dir = list(root_dirs)[0]
                                print(f"[解压] 检测到根目录: {root_dir}/，将内容直接解压到目标目录")
                                
                                for member in zf.namelist():
                                    # 去掉根目录前缀
                                    if member.startswith(root_dir + '/'):
                                        # 获取去掉根目录后的路径
                                        target_path = member[len(root_dir) + 1:]
                                        if target_path:  # 跳过空路径（根目录本身）
                                            # 提取文件内容
                                            source = zf.read(member)
                                            target_file = output_dir / target_path
                                            
                                            # 创建目录
                                            if member.endswith('/'):
                                                target_file.mkdir(parents=True, exist_ok=True)
                                            else:
                                                target_file.parent.mkdir(parents=True, exist_ok=True)
                                                target_file.write_bytes(source)
                            else:
                                # 没有统一的根目录，直接解压
                                zf.extractall(output_dir)
                        
                        print(f"[解压] 完成! 文件已解压到: {output_dir}")
                        
                        # 解压成功后删除 ZIP 文件
                        try:
                            output_path.unlink()
                            print(f"[清理] 已删除 ZIP 文件: {output_path.name}")
                        except Exception as e:
                            print(f"[警告] 删除 ZIP 文件失败: {e}")
                            
                    except zipfile.BadZipFile:
                        print("[警告] 文件不是有效的 ZIP 格式，跳过解压")
                
                return True
            
        except (requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            retry_count += 1
            print(f"\n[警告] 连接中断: {type(e).__name__}")
            
            if retry_count <= max_retries:
                # 更新已下载大小
                if temp_path.exists():
                    downloaded = temp_path.stat().st_size
                
                print(f"[重试] {retry_count}/{max_retries}，已下载 {format_size(downloaded)}，{RETRY_DELAY}秒后重试...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"[错误] 超过最大重试次数 ({max_retries})")
                print(f"[提示] 已下载的部分保存在: {temp_path}")
                print(f"[提示] 重新运行命令将自动续传")
                return False
                
        except requests.exceptions.SSLError:
            print("\n[错误] SSL 证书验证失败")
            print("       可以使用 --no-verify 跳过证书验证")
            return False
        except Exception as e:
            print(f"\n[错误] 下载失败: {e}")
            return False
    
    return True


def main():
    parser = argparse.ArgumentParser(
        description="下载远程文件（支持断点续传和自动重试）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本下载
  python download_results.py "https://example.com/files/results.zip"

  # 指定输出目录
  python download_results.py "https://example.com/files/results.zip" -o D:/results

  # 带 Jupyter token 认证
  python download_results.py "https://example.com/files/results.zip" --token your_token

  # 启用 SSL 验证（默认跳过）
  python download_results.py "https://example.com/files/results.zip" --verify

  # 使用 Cookie 认证（从浏览器复制）
  python download_results.py "https://example.com/files/results.zip" --cookie "_xsrf=xxx"

  # 设置重试次数
  python download_results.py "https://example.com/files/results.zip" --retries 20

  # 不自动解压
  python download_results.py "https://example.com/files/results.zip" --no-extract
        """
    )
    
    parser.add_argument("url", help="下载链接")
    parser.add_argument("-o", "--output", type=str, default=str(DEFAULT_OUTPUT_DIR), help=f"输出目录（默认: {DEFAULT_OUTPUT_DIR}）")
    parser.add_argument("-f", "--filename", type=str, help="保存的文件名（默认从URL获取）")
    parser.add_argument("--token", type=str, help="Jupyter Server token（jupyter server list）")
    parser.add_argument("--cookie", type=str, help="Cookie 字符串（从浏览器复制）")
    parser.add_argument("--verify", action="store_true", help="启用 SSL 证书验证（默认跳过）")
    parser.add_argument("--no-extract", action="store_true", help="不自动解压 ZIP 文件")
    parser.add_argument("--retries", type=int, default=MAX_RETRIES, help=f"最大重试次数（默认: {MAX_RETRIES}）")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Results Download Tool")
    print("=" * 60)
    
    success = download_file_with_resume(
        url=args.url,
        output_dir=Path(args.output),
        filename=args.filename,
        token=args.token,
        cookie=args.cookie,
        extract=not args.no_extract,
        verify_ssl=args.verify,  # 默认不验证 SSL
        max_retries=args.retries
    )
    
    print("=" * 60)
    
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
