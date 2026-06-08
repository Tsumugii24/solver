#!/bin/sh
# 启动 Mihomo 前从 CLASH_SUBSCRIPTION_URL 拉取配置，或使用挂载的 clash/config.yaml
set -eu

MIHOMO_DIR="/root/.config/mihomo"
CONFIG="${MIHOMO_DIR}/config.yaml"
CLASH_WORK="/clash-work"

mkdir -p "$MIHOMO_DIR"

download_url() {
    url="$1"
    dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$dest"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$dest" "$url"
    else
        echo "[clash] 需要 curl 或 wget 才能下载订阅" >&2
        exit 1
    fi
}

if [ -n "${CLASH_SUBSCRIPTION_URL:-}" ]; then
    echo "[clash] 从 CLASH_SUBSCRIPTION_URL 下载配置..."
    download_url "$CLASH_SUBSCRIPTION_URL" "$CONFIG"
    if [ -d "$CLASH_WORK" ]; then
        cp "$CONFIG" "$CLASH_WORK/config.yaml" 2>/dev/null || true
    fi
elif [ -f "$CLASH_WORK/config.yaml" ]; then
    echo "[clash] 使用挂载的 clash/config.yaml"
    cp "$CLASH_WORK/config.yaml" "$CONFIG"
else
    echo "[clash] 请在 .env 中设置 CLASH_SUBSCRIPTION_URL，或提供 clash/config.yaml" >&2
    exit 1
fi

if [ ! -s "$CONFIG" ]; then
    echo "[clash] 配置为空，请检查 CLASH_SUBSCRIPTION_URL" >&2
    exit 1
fi

MIHOMO_BIN="$(command -v mihomo 2>/dev/null || true)"
if [ -z "$MIHOMO_BIN" ] && [ -x /mihomo ]; then
    MIHOMO_BIN=/mihomo
fi
if [ -z "$MIHOMO_BIN" ]; then
    echo "[clash] 找不到 mihomo 可执行文件" >&2
    exit 1
fi

echo "[clash] 启动 mihomo..."
exec "$MIHOMO_BIN" -d "$MIHOMO_DIR"
