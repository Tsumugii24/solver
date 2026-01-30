#!/bin/bash
# TexasSolver Console Linux 构建脚本

set -e  # 遇到错误立即退出

# 获取脚本所在目录（即项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "TexasSolver Console Linux Build"
echo "=========================================="

# 清理旧的构建目录
if [ -d "build" ]; then
    echo "[1/4] 清理旧的构建目录..."
    rm -rf build
fi

if [ -d "install" ]; then
    rm -rf install
fi

# 创建构建目录
echo "[2/4] 创建构建目录..."
mkdir -p build
cd build

# 配置 CMake
echo "[3/4] 配置 CMake..."
cmake .. -DCMAKE_BUILD_TYPE=Release

# 编译并安装
echo "[4/4] 编译中..."
make -j$(nproc) install

echo ""
echo "=========================================="
echo "构建完成!"
echo "可执行文件: $SCRIPT_DIR/install/console_solver"
echo "=========================================="
