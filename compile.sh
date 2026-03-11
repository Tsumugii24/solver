#!/usr/bin/env bash
# TexasSolver Console Linux Build Script
# Installs required system dependencies before building.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENABLE_PARQUET_EXPORT=1
INSTALL_DEPS=1
BUILD_DIR="build"
BUILD_TYPE="Release"
TOOLCHAIN_FILE="${CMAKE_TOOLCHAIN_FILE:-}"
PREFIX_PATH=""
PACKAGE_MANAGER=""
CMAKE_BIN=""

log() {
    echo "$@"
}

warn() {
    echo "Warning: $*" >&2
}

die() {
    echo "Error: $*" >&2
    exit 1
}

have_cmd() {
    command -v "$1" >/dev/null 2>&1
}

need_value() {
    local option="$1"
    local value="${2:-}"
    [[ -n "$value" ]] || die "Option ${option} requires a value"
}

is_wsl() {
    [[ -n "${WSL_INTEROP:-}" ]] || grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null
}

detect_jobs() {
    if have_cmd nproc; then
        nproc
    else
        getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1
    fi
}

run_privileged() {
    if [[ "${EUID}" -eq 0 ]]; then
        "$@"
    elif have_cmd sudo; then
        sudo "$@"
    else
        die "Need root privileges to install dependencies; rerun as root or install sudo"
    fi
}

detect_package_manager() {
    if [[ -n "$PACKAGE_MANAGER" ]]; then
        echo "$PACKAGE_MANAGER"
        return
    fi

    if have_cmd apt-get; then
        echo "apt"
    elif have_cmd dnf; then
        echo "dnf"
    elif have_cmd yum; then
        echo "yum"
    elif have_cmd pacman; then
        echo "pacman"
    elif have_cmd zypper; then
        echo "zypper"
    else
        die "Unsupported Linux distribution: could not detect apt, dnf, yum, pacman, or zypper"
    fi
}

detect_cmake_bin() {
    if have_cmd cmake; then
        CMAKE_BIN="cmake"
    elif have_cmd cmake3; then
        CMAKE_BIN="cmake3"
    else
        die "cmake is not installed"
    fi
}

has_cmake_config() {
    local package="$1"
    local patterns=(
        "/usr/lib/*/cmake/${package}/${package}Config.cmake"
        "/usr/lib64/cmake/${package}/${package}Config.cmake"
        "/usr/lib/cmake/${package}/${package}Config.cmake"
        "/usr/local/lib/*/cmake/${package}/${package}Config.cmake"
        "/usr/local/lib64/cmake/${package}/${package}Config.cmake"
        "/usr/local/lib/cmake/${package}/${package}Config.cmake"
    )
    local pattern

    for pattern in "${patterns[@]}"; do
        if compgen -G "$pattern" >/dev/null; then
            return 0
        fi
    done
    return 1
}

detect_apt_arrow_repo_distro() {
    local distro=""

    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        source /etc/os-release
        if [[ " ${ID_LIKE:-} " == *" ubuntu "* ]] || [[ "${ID:-}" == "ubuntu" ]]; then
            distro="ubuntu"
        elif [[ " ${ID_LIKE:-} " == *" debian "* ]] || [[ "${ID:-}" == "debian" ]]; then
            distro="debian"
        fi
    fi

    [[ -n "$distro" ]] || distro="ubuntu"
    echo "$distro"
}

detect_apt_codename() {
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        source /etc/os-release
        if [[ -n "${VERSION_CODENAME:-}" ]]; then
            echo "$VERSION_CODENAME"
            return
        fi
    fi

    if have_cmd lsb_release; then
        lsb_release --codename --short
        return
    fi

    die "Could not detect distribution codename for Apache Arrow repository setup"
}

download_file() {
    local url="$1"
    local output="$2"

    if have_cmd curl; then
        curl -fsSL "$url" -o "$output"
    elif have_cmd wget; then
        wget -qO "$output" "$url"
    else
        die "Need curl or wget to download dependencies"
    fi
}

setup_arrow_apt_repo() {
    local distro
    local codename
    local deb_name
    local repo_url
    local tmp_dir

    distro="$(detect_apt_arrow_repo_distro)"
    codename="$(detect_apt_codename)"
    deb_name="apache-arrow-apt-source-latest-${codename}.deb"
    repo_url="https://packages.apache.org/artifactory/arrow/${distro}/${deb_name}"
    tmp_dir="$(mktemp -d)"

    log "[deps] Adding Apache Arrow APT repository for ${distro}:${codename}..."
    download_file "$repo_url" "${tmp_dir}/${deb_name}"
    run_privileged apt-get install -y "${tmp_dir}/${deb_name}"
    run_privileged apt-get update
    rm -rf "$tmp_dir"
}

install_packages_apt() {
    run_privileged apt-get update
    run_privileged apt-get install -y --no-install-recommends "$@"
}

install_packages_dnf() {
    run_privileged dnf install -y "$@"
}

install_packages_yum() {
    run_privileged yum install -y "$@"
}

install_packages_pacman() {
    run_privileged pacman -Sy --noconfirm --needed "$@"
}

install_packages_zypper() {
    run_privileged zypper --non-interactive install --no-recommends "$@"
}

install_build_deps() {
    local pm
    pm="$(detect_package_manager)"

    log "[deps] Installing build dependencies via ${pm}..."

    case "$pm" in
        apt)
            install_packages_apt \
                build-essential cmake pkg-config ca-certificates curl wget gnupg lsb-release
            if [[ "$ENABLE_PARQUET_EXPORT" -eq 1 ]]; then
                install_packages_apt \
                    libssl-dev zlib1g-dev libbz2-dev liblz4-dev libzstd-dev \
                    libutf8proc-dev libcurl4-openssl-dev libthrift-dev thrift-compiler
                if ! has_cmake_config Arrow || ! has_cmake_config Parquet; then
                    setup_arrow_apt_repo
                fi
                install_packages_apt libarrow-dev libparquet-dev
            fi
            ;;
        dnf)
            install_packages_dnf \
                gcc gcc-c++ make cmake pkgconf-pkg-config curl wget \
                openssl-devel zlib-devel bzip2-devel lz4-devel zstd-devel libcurl-devel
            if [[ "$ENABLE_PARQUET_EXPORT" -eq 1 ]]; then
                install_packages_dnf thrift-devel
                if ! install_packages_dnf libarrow-devel libparquet-devel utf8proc-devel; then
                    install_packages_dnf arrow-devel parquet-devel thrift-devel
                fi
            fi
            ;;
        yum)
            if ! install_packages_yum gcc gcc-c++ make cmake pkgconfig curl wget; then
                install_packages_yum gcc gcc-c++ make cmake3 pkgconfig curl wget
            fi
            if [[ "$ENABLE_PARQUET_EXPORT" -eq 1 ]]; then
                if ! install_packages_yum \
                    openssl-devel zlib-devel bzip2-devel lz4-devel libzstd-devel libcurl-devel thrift-devel; then
                    warn "Some yum development packages could not be installed automatically"
                fi
                if ! install_packages_yum libarrow-devel libparquet-devel; then
                    install_packages_yum arrow-devel parquet-devel
                fi
            fi
            ;;
        pacman)
            install_packages_pacman base-devel cmake pkgconf curl wget
            if [[ "$ENABLE_PARQUET_EXPORT" -eq 1 ]]; then
                install_packages_pacman arrow thrift
            fi
            ;;
        zypper)
            install_packages_zypper gcc gcc-c++ make cmake pkg-config curl wget
            if [[ "$ENABLE_PARQUET_EXPORT" -eq 1 ]]; then
                if ! install_packages_zypper apache-arrow-devel thrift-devel; then
                    install_packages_zypper apache-arrow thrift-devel
                fi
            fi
            ;;
        *)
            die "Unsupported package manager: ${pm}"
            ;;
    esac

    detect_cmake_bin
}

sanitize_path() {
    local input_path="$1"
    local entry=""
    local result=()
    local seen=":"

    IFS=':' read -r -a path_parts <<<"$input_path"
    for entry in "${path_parts[@]}"; do
        [[ -n "$entry" ]] || continue

        case "$entry" in
            *"/miniconda"*|*"/anaconda"*|*"/mambaforge"*|*"/miniforge"*|*"/condabin"*)
                continue
                ;;
        esac

        if is_wsl; then
            case "$entry" in
                /mnt/[A-Za-z]/*)
                    continue
                    ;;
            esac
        fi

        if [[ "$seen" != *":${entry}:"* ]]; then
            result+=("$entry")
            seen="${seen}${entry}:"
        fi
    done

    (
        IFS=':'
        echo "${result[*]}"
    )
}

build_with_clean_env() {
    local clean_path
    local -a cmake_env
    local -a cmake_args

    clean_path="$(sanitize_path "$PATH")"
    cmake_env=(
        env
        "PATH=${clean_path}"
        "HOME=${HOME}"
        "CONDA_PREFIX="
        "CONDA_DEFAULT_ENV="
        "CONDA_PROMPT_MODIFIER="
        "CONDA_SHLVL=0"
        "Arrow_DIR="
        "Parquet_DIR="
        "Thrift_DIR="
        "CMAKE_PREFIX_PATH="
    )

    cmake_args=(
        -S .
        -B "$BUILD_DIR"
        -DCMAKE_BUILD_TYPE="$BUILD_TYPE"
    )

    if [[ "$ENABLE_PARQUET_EXPORT" -eq 1 ]]; then
        cmake_args+=(-DENABLE_PARQUET_EXPORT=ON)
        if [[ -z "$TOOLCHAIN_FILE" ]]; then
            # Prefer pkg-config/system libraries over stray ThriftConfig.cmake files.
            cmake_args+=(
                -DCMAKE_DISABLE_FIND_PACKAGE_Thrift=TRUE
                -DCMAKE_DISABLE_FIND_PACKAGE_lz4=TRUE
                -DCMAKE_DISABLE_FIND_PACKAGE_re2=TRUE
                -DCMAKE_DISABLE_FIND_PACKAGE_protobuf=TRUE
            )
        fi
    fi

    if [[ -n "$TOOLCHAIN_FILE" ]]; then
        cmake_args+=("-DCMAKE_TOOLCHAIN_FILE=$TOOLCHAIN_FILE")
    fi

    if [[ -n "$PREFIX_PATH" ]]; then
        cmake_args+=("-DCMAKE_PREFIX_PATH=$PREFIX_PATH")
    elif [[ "$ENABLE_PARQUET_EXPORT" -eq 1 && -z "$TOOLCHAIN_FILE" ]]; then
        cmake_args+=("-DCMAKE_PREFIX_PATH=/usr")
    fi

    "${cmake_env[@]}" "$CMAKE_BIN" -Wno-dev "${cmake_args[@]}"
    "${cmake_env[@]}" "$CMAKE_BIN" --build "$BUILD_DIR" --parallel "$(detect_jobs)"
    "${cmake_env[@]}" "$CMAKE_BIN" --install "$BUILD_DIR" --prefix "$SCRIPT_DIR/install"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --enable-parquet-export)
            ENABLE_PARQUET_EXPORT=1
            shift
            ;;
        --disable-parquet-export)
            ENABLE_PARQUET_EXPORT=0
            shift
            ;;
        --skip-deps)
            INSTALL_DEPS=0
            shift
            ;;
        --build-dir)
            need_value "$1" "${2:-}"
            BUILD_DIR="$2"
            shift 2
            ;;
        --build-type)
            need_value "$1" "${2:-}"
            BUILD_TYPE="$2"
            shift 2
            ;;
        --toolchain-file)
            need_value "$1" "${2:-}"
            TOOLCHAIN_FILE="$2"
            shift 2
            ;;
        --cmake-prefix-path)
            need_value "$1" "${2:-}"
            PREFIX_PATH="$2"
            shift 2
            ;;
        --package-manager)
            need_value "$1" "${2:-}"
            PACKAGE_MANAGER="$2"
            shift 2
            ;;
        -h|--help)
            cat <<'EOF'
Usage: ./compile.sh [options]

Options:
  --enable-parquet-export      Enable Arrow/Parquet C++ export support (default)
  --disable-parquet-export     Disable Arrow/Parquet C++ export support
  --skip-deps                  Skip system dependency installation
  --build-dir <dir>            Build directory (default: build)
  --build-type <type>          CMake build type (default: Release)
  --toolchain-file <file>      Optional CMake toolchain file
  --cmake-prefix-path <path>   Optional CMAKE_PREFIX_PATH override
  --package-manager <name>     Force package manager: apt|dnf|yum|pacman|zypper

Examples:
  ./compile.sh
  ./compile.sh --disable-parquet-export
  ./compile.sh --skip-deps --cmake-prefix-path /opt/arrow
  ./compile.sh --toolchain-file "$HOME/vcpkg/scripts/buildsystems/vcpkg.cmake"
EOF
            exit 0
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
done

[[ "$(uname -s)" == "Linux" ]] || die "This script only supports Linux"

echo "=========================================="
echo "TexasSolver Console Linux Build"
echo "=========================================="
echo "Build type: $BUILD_TYPE"
if [[ "$ENABLE_PARQUET_EXPORT" -eq 1 ]]; then
    echo "Native Parquet Export: ON (json + parquet + parquet_native)"
else
    echo "Native Parquet Export: OFF (json only)"
fi
if [[ "$INSTALL_DEPS" -eq 1 ]]; then
    echo "Auto-install dependencies: ON"
else
    echo "Auto-install dependencies: OFF"
fi

if [[ "$INSTALL_DEPS" -eq 1 ]]; then
    echo "[1/5] Installing dependencies..."
    install_build_deps
else
    detect_cmake_bin
fi

if [[ -d "$BUILD_DIR" ]]; then
    echo "[2/5] Cleaning old build directory..."
    rm -rf "$BUILD_DIR"
else
    echo "[2/5] No previous build directory to clean"
fi

if [[ -d "install" ]]; then
    rm -rf install
fi

echo "[3/5] Creating build directory..."
mkdir -p "$BUILD_DIR"

echo "[4/5] Configuring and building..."
build_with_clean_env

echo "[5/5] Build completed!"
echo ""
echo "=========================================="
echo "Executable: $SCRIPT_DIR/install/console_solver"
echo "=========================================="
