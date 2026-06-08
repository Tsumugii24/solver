#!/usr/bin/env bash
# 使用 solver-secrets 挂载配置，启动 Clash sidecar 并运行 run_pipeline.py
#
# 用法:
#   ./scripts/run-pipeline.sh 1-20
#   ./scripts/run-pipeline.sh 1-20 --repo-id Tsumugii/sia-12-sod-30
#   ./scripts/run-pipeline.sh Jc7c5c --no-upload
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f "$REPO_ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

SECRETS_DIR="${SECRETS_DIR:-${HOME}/solver-secrets}"
export SECRETS_DIR

if [[ ! -f "$SECRETS_DIR/.env" ]]; then
    echo "[错误] 未找到 $SECRETS_DIR/.env，请先运行: ./scripts/bootstrap-secrets.sh" >&2
    exit 1
fi

if [[ ! -f "$SECRETS_DIR/clash/config.yaml" ]]; then
    echo "[错误] 未找到 $SECRETS_DIR/clash/config.yaml" >&2
    echo "  在 secrets 目录运行: ./scripts/update-clash-config.sh" >&2
    exit 1
fi

# shellcheck disable=SC1091
set -a
source "$SECRETS_DIR/.env"
set +a

REPO_ID="${HF_REPO_ID:-}"
PIPELINE_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-id)
            REPO_ID="${2:?--repo-id 需要值}"
            shift 2
            ;;
        *)
            PIPELINE_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ -z "$REPO_ID" ]]; then
    echo "[错误] 请设置 HF_REPO_ID（secrets/.env）或传入 --repo-id" >&2
    exit 1
fi

COMPOSE=(docker compose --env-file "$SECRETS_DIR/.env")

if [[ -n "${SOLVER_IMAGE:-}" ]]; then
    export SOLVER_IMAGE
fi

cleanup() {
    "${COMPOSE[@]}" --profile proxy stop clash 2>/dev/null || true
}
trap cleanup EXIT

echo "[pipeline] SECRETS_DIR=$SECRETS_DIR"
echo "[pipeline] HF_REPO_ID=$REPO_ID"
echo "[pipeline] args: ${PIPELINE_ARGS[*]:-(none)}"

"${COMPOSE[@]}" --profile proxy up -d clash
"${COMPOSE[@]}" --profile proxy run --rm pipeline \
    python3 run_pipeline.py "${PIPELINE_ARGS[@]}" --repo-id "$REPO_ID"
