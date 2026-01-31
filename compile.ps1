# TexasSolver Console Windows Build Script

$ErrorActionPreference = "Stop"

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $SCRIPT_DIR

Write-Host "=========================================="
Write-Host "TexasSolver Console Windows Build"
Write-Host "=========================================="

if (Test-Path "build") {
    Write-Host "[1/3] Cleaning old build directory..."
    Remove-Item -Recurse -Force build
}

Write-Host "[2/3] Creating build directory and configuring CMake..."
New-Item -ItemType Directory -Force -Path build | Out-Null
Set-Location build

cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"
if ($LASTEXITCODE -ne 0) {
    Write-Host "CMake configuration failed!" -ForegroundColor Red
    exit 1
}

Write-Host "[3/3] Building..."
cmake --build . --config Release
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed!" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=========================================="
Write-Host "Build completed!"
Write-Host "Executable: $SCRIPT_DIR\build\console_solver.exe"
Write-Host "=========================================="
