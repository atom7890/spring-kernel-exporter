#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
    printf 'Usage: %s BUILD_DIR NEW_EXPORT_DIR\n' "$0" >&2
    exit 2
fi

tools=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_dir=${KERNEL_SOURCE:-/home/atom/Desktop/android_kernel_xiaomi_sm6375}
clang_bin=${CLANG_BIN:-/home/atom/kernel-toolchains/clang-r487747c/clang-r487747c/bin}
build_dir=$(realpath -m -- "$1")
destination=$(realpath -m -- "$2")

if [[ "$build_dir" =~ [[:space:]] || -e "$destination" ]]; then
    printf 'Use a build directory without spaces and a new export directory.\n' >&2
    exit 2
fi
test -x "$clang_bin/clang"
export PATH="$clang_bin:/usr/bin:/bin"
export LOCALVERSION=
unset LIBCLANG_PATH ANDROID_BUILD_TOP LINUX_BUILD_TOP
for command in clang ld.lld llvm-strip pahole lz4 depmod modinfo openssl python3; do
    command -v "$command" >/dev/null || { printf 'Missing tool: %s\n' "$command" >&2; exit 1; }
done

work=$(mktemp -d /tmp/spring-standalone-install.XXXXXX)
trap 'rm -rf -- "$work"' EXIT
args=(-C "$source_dir" O="$build_dir" ARCH=arm64 LLVM=1 LLVM_IAS=1 TARGET_BOARD_PLATFORM=blair)
make "${args[@]}" spring_defconfig
make -j"${JOBS:-8}" "${args[@]}" Image modules dtbs
make "${args[@]}" headers_install INSTALL_HDR_PATH="$build_dir/headers"
make -j"${JOBS:-8}" "${args[@]}" INSTALL_MOD_PATH="$work/installed" INSTALL_MOD_STRIP=1 modules_install
python3 "$tools/export.py" "$build_dir" "$work/installed" - "$destination" --source "$source_dir"
(cd "$destination" && sha256sum --quiet -c SHA256SUMS)
