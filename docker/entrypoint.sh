#!/usr/bin/env bash
set -euo pipefail

cd /app

if [[ ! -x "install/console_solver" ]]; then
    echo "[错误] 镜像内缺少 install/console_solver，请重新构建 Docker 镜像" >&2
    exit 1
fi

if [[ $# -eq 0 ]]; then
    set -- python3 run_pipeline.py --help
fi

exec "$@"
