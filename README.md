# Solver

## Windows 编译说明

### 环境要求

```
scoop install cmake
scoop install gcc
scoop install ninja
```
### 一键编译
```powershell
./compile.ps1
```

### 手动编译
```powershell
Remove-Item -Recurse -Force build
mkdir build
cd build
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"
cmake --build . --config Release
```

### 编译产物

编译完成后，`console_solver.exe` 位于 `build/` 目录下


## Linux 编译说明

### 环境要求

安装 `cmake`、`make`、支持 OpenMP 的编译器（如 gcc）

### 一键编译
```bash
chmod +x compile.sh
bash compile.sh
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


## macOS 编译说明

### 一键编译（自动安装依赖）

脚本会自动安装 `Homebrew`、`cmake`、`libomp` 等依赖，无需手动配置：

```bash
chmod +x compile_macos.sh
bash compile_macos.sh
```

### 编译产物

编译完成后，`console_solver` 位于 `install/` 目录下