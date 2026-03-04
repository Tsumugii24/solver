#!/bin/bash
# TexasSolver Console macOS Build Script
# 自动安装 Homebrew、cmake、libomp 等依赖

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "TexasSolver Console macOS Build"
echo "=========================================="

# 确保 brew 在 PATH 中（Apple Silicon: /opt/homebrew, Intel: /usr/local）
if [ -f /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -f /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
fi

# 自动安装 Homebrew（若未安装）
if ! command -v brew &>/dev/null; then
    echo "[依赖] 安装 Homebrew..."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # 安装后加载 brew 到当前 shell
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
else
    echo "[依赖] Homebrew 已安装"
fi

# 自动安装 cmake
if ! command -v cmake &>/dev/null; then
    echo "[依赖] 安装 cmake..."
    brew install cmake
else
    echo "[依赖] cmake 已安装"
fi

# 自动安装 libomp（Apple clang 不支持 OpenMP，需要 libomp）
if ! brew list libomp &>/dev/null 2>&1; then
    echo "[依赖] 安装 libomp (OpenMP 支持)..."
    brew install libomp
else
    echo "[依赖] libomp 已安装"
fi

# 清理旧构建
if [ -d "build" ]; then
    echo "[1/3] 清理旧 build 目录..."
    rm -rf build
fi

if [ -d "install" ]; then
    rm -rf install
fi

echo "[2/3] 配置 CMake..."
mkdir -p build
cd build
# libomp 为 keg-only，需显式指定路径供 FindOpenMP 使用
export LDFLAGS="-L$(brew --prefix libomp)/lib"
export CPPFLAGS="-I$(brew --prefix libomp)/include"
cmake .. -DCMAKE_BUILD_TYPE=Release

echo "[3/3] 编译..."
make -j$(sysctl -n hw.ncpu) install

echo ""
echo "=========================================="
echo "Build completed!"
echo "Executable: $SCRIPT_DIR/install/console_solver"
echo "=========================================="
