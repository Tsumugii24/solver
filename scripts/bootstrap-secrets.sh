#!/usr/bin/env bash
# 克隆或更新 GitHub 私有仓 solver-secrets（只需 .env，Clash 配置由容器启动时自动拉取）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SECRETS_DIR="${SECRETS_DIR:-${HOME}/solver-secrets}"

if [[ -f "$REPO_ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

if [[ -z "${GH_TOKEN:-}" ]]; then
    echo "[错误] 请设置 GH_TOKEN（GitHub PAT，需 repo 读权限）" >&2
    echo "  export GH_TOKEN=ghp_xxxx" >&2
    echo "  或在 solver 目录创建 .env（参考 .env.docker.example）" >&2
    exit 1
fi

clone_url="https://oauth2:${GH_TOKEN}@github.com/Tsumugii24/solver-secrets.git"

if [[ ! -d "$SECRETS_DIR/.git" ]]; then
    echo "[secrets] 克隆到 $SECRETS_DIR ..."
    git clone "$clone_url" "$SECRETS_DIR"
else
    echo "[secrets] 更新 $SECRETS_DIR ..."
    git -C "$SECRETS_DIR" pull --ff-only
fi

mkdir -p "$SECRETS_DIR/clash"

if [[ ! -f "$SECRETS_DIR/.env" ]]; then
    if [[ -f "$SECRETS_DIR/.env.example" ]]; then
        cp "$SECRETS_DIR/.env.example" "$SECRETS_DIR/.env"
        echo "[secrets] 已从 .env.example 创建 $SECRETS_DIR/.env"
        echo "[secrets] 请编辑 .env 填写 HF_TOKEN、CLASH_SUBSCRIPTION_URL，然后直接 run-pipeline" >&2
        exit 1
    fi
    echo "[错误] $SECRETS_DIR/.env 不存在" >&2
    exit 1
fi

echo "[secrets] 就绪: $SECRETS_DIR"
echo "  .env: $SECRETS_DIR/.env"
echo "  Clash: 容器启动时从 CLASH_SUBSCRIPTION_URL 自动下载（无需手动 config.yaml）"
