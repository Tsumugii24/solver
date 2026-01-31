#!/bin/bash
# TexasSolver Console Linux Build Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "TexasSolver Console Linux Build"
echo "=========================================="

if [ -d "build" ]; then
    echo "[1/4] Cleaning old build directory..."
    rm -rf build
fi

if [ -d "install" ]; then
    rm -rf install
fi

echo "[2/4] Creating build directory..."
mkdir -p build
cd build

echo "[3/4] Configuring CMake..."
cmake .. -DCMAKE_BUILD_TYPE=Release

echo "[4/4] Building..."
make -j$(nproc) install

echo ""
echo "=========================================="
echo "Build completed!"
echo "Executable: $SCRIPT_DIR/install/console_solver"
echo "=========================================="
