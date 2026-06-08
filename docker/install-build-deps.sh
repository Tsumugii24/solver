#!/usr/bin/env bash
# Install apt packages required to compile console_solver (mirrors compile.sh).
set -euo pipefail

UBUNTU_CODENAME="${UBUNTU_CODENAME:-noble}"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg lsb-release \
    build-essential cmake pkg-config \
    libssl-dev zlib1g-dev libbz2-dev liblz4-dev libzstd-dev \
    libutf8proc-dev libcurl4-openssl-dev libthrift-dev thrift-compiler

deb="/tmp/apache-arrow-apt-source-latest-${UBUNTU_CODENAME}.deb"
curl -fsSL "https://packages.apache.org/artifactory/arrow/ubuntu/apache-arrow-apt-source-latest-${UBUNTU_CODENAME}.deb" \
    -o "$deb"
apt-get install -y "$deb"
apt-get update
apt-get install -y --no-install-recommends libarrow-dev libparquet-dev

rm -rf /var/lib/apt/lists/*
