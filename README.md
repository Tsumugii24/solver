# Solver

## Windows 编译说明

### 环境要求

```
scoop install cmake
scoop install gcc
scoop install ninja
```
### 编译步骤

```powershell
# 1. 进入项目目录
cd solver

# 2. 清理旧的构建缓存（如果之前编译过或移动过项目目录）
Remove-Item -Recurse -Force build
mkdir build
cd build

# 3. 配置 CMake（使用 Ninja 生成器，Release 模式）
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"

# 4. 编译
cmake --build . --config Release
```

### 编译产物

编译完成后，`console_solver.exe` 位于 `build/` 目录下


## Linux 编译说明

### 一键编译
```bash
chmod +x compile.sh
./compile.sh
```

### 手动编译
```bash
rm -rf build install
mkdir build
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j$(nproc) install
```

### 编译产物

编译完成后，`console_solver` 位于 `install/` 目录下