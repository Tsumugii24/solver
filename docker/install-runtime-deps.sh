#!/usr/bin/env bash
# Runtime libraries for console_solver + Python pipeline (no compiler toolchain).
set -euo pipefail

UBUNTU_CODENAME="${UBUNTU_CODENAME:-jammy}"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg lsb-release \
    libgomp1 \
    libssl3 zlib1g libbz2-1.0 liblz4-1 libzstd1 libutf8proc2 \
    libcurl4 \
    python3 python3-pip

deb="/tmp/apache-arrow-apt-source-latest-${UBUNTU_CODENAME}.deb"
curl -fsSL "https://packages.apache.org/artifactory/arrow/ubuntu/apache-arrow-apt-source-latest-${UBUNTU_CODENAME}.deb" \
    -o "$deb"
apt-get install -y "$deb"
apt-get update

# Pick Arrow/Parquet runtime packages without pinning a version number.
mapfile -t arrow_libs < <(apt-cache search --names-only '^libarrow[0-9]+$' | awk '{print $1}' | sort -V)
mapfile -t parquet_libs < <(apt-cache search --names-only '^libparquet[0-9]+$' | awk '{print $1}' | sort -V)
[[ ${#arrow_libs[@]} -gt 0 ]] || { echo "No libarrow runtime package found" >&2; exit 1; }
[[ ${#parquet_libs[@]} -gt 0 ]] || { echo "No libparquet runtime package found" >&2; exit 1; }

thrift_lib="$(apt-cache search --names-only '^libthrift-' | awk '{print $1}' | grep -E '^libthrift-[0-9]' | sort -V | tail -n1 || true)"
extra=()
if [[ -n "$thrift_lib" ]]; then
    extra+=("$thrift_lib")
fi

apt-get install -y --no-install-recommends \
    "${arrow_libs[-1]}" "${parquet_libs[-1]}" "${extra[@]}"

rm -rf /var/lib/apt/lists/*
