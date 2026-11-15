#!/usr/bin/env bash
# Build the Spring kernel, modules and DTBs and export a prebuilt directory.
# Runs on any x86_64 Linux machine that has the tools listed in README.md.
set -euo pipefail

usage() {
    cat >&2 <<USAGE
Usage: $0 [--check] [--replace-export] [BUILD_DIR EXPORT_DIR]

  BUILD_DIR       kernel output directory (default: <source>/out-spring-kernel-oss)
  EXPORT_DIR      directory to create for the export (default: ./spring-kernel-oss)
                  It must not exist yet, unless you pass --replace-export.
  --check         print the detected settings and tools, then exit
  --replace-export  delete EXPORT_DIR first if it is an earlier export (has SHA256SUMS)

BUILD_DIR may already exist: it is reused, so the kernel build is incremental.

Environment:
  KERNEL_SOURCE   Spring kernel checkout (default: current directory, or the
                  first Spring kernel folder found next to this one)
  CLANG_BIN       directory holding clang and ld.lld (default: whatever is on PATH)
  JOBS            parallel jobs (default: number of CPUs)
  TMPDIR          scratch location (default: /tmp)
USAGE
    exit 2
}

check_only=0
replace_export=0
while [[ ${1:-} == --* || ${1:-} == -h ]]; do
    case $1 in
        --check) check_only=1 ;;
        --replace-export) replace_export=1 ;;
        -h|--help) usage ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage ;;
    esac
    shift
done
if (( $# != 0 && $# != 2 )); then usage; fi

tools=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

is_kernel_tree() {
    [[ -f $1/Makefile && -f $1/arch/arm64/configs/spring_defconfig ]]
}

# --- kernel source ----------------------------------------------------------
if [[ -n ${KERNEL_SOURCE:-} ]]; then
    source_dir=$KERNEL_SOURCE
elif is_kernel_tree "$PWD"; then
    source_dir=$PWD
else
    source_dir=
    for candidate in "$tools"/../*/; do
        if is_kernel_tree "$candidate"; then source_dir=$candidate; break; fi
    done
    [[ -n $source_dir ]] || {
        printf 'Kernel source not found. Run this from the kernel folder or set KERNEL_SOURCE.\n' >&2
        exit 2
    }
fi
source_dir=$(realpath -- "$source_dir")
is_kernel_tree "$source_dir" || {
    printf '%s is not the Spring kernel tree (no arch/arm64/configs/spring_defconfig).\n' "$source_dir" >&2
    exit 2
}

# --- toolchain --------------------------------------------------------------
if [[ -n ${CLANG_BIN:-} ]]; then
    clang_bin=$(realpath -- "$CLANG_BIN")
    [[ -x $clang_bin/clang ]] || { printf 'No clang in CLANG_BIN=%s\n' "$CLANG_BIN" >&2; exit 2; }
    export PATH="$clang_bin:$PATH"
fi
command -v clang >/dev/null || {
    printf 'clang not found. Put it on PATH or set CLANG_BIN.\n' >&2
    printf 'This kernel was validated with Android clang r487747c (clang 17.0.2).\n' >&2
    exit 1
}
clang_version=$(clang --version | head -n1)
case $clang_version in
    *17.0.2*) ;;
    *) printf 'Warning: %s\n         Validated toolchain is Android clang r487747c (17.0.2); other versions are untested.\n' "$clang_version" >&2 ;;
esac

jobs=${JOBS:-$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}

# --- other host tools -------------------------------------------------------
missing=()
for command in make clang ld.lld llvm-ar llvm-strip pahole lz4 depmod modinfo openssl python3 flex bison perl realpath sha256sum mktemp; do
    command -v "$command" >/dev/null || missing+=("$command")
done

# --- output locations -------------------------------------------------------
build_dir=$(realpath -m -- "${1:-$source_dir/out-spring-kernel-oss}")
destination=$(realpath -m -- "${2:-$PWD/spring-kernel-oss}")

if (( check_only )); then
    printf 'source      : %s\nbuild dir   : %s\nexport dir  : %s\nclang       : %s\njobs        : %s\n' \
        "$source_dir" "$build_dir" "$destination" "$clang_version" "$jobs"
    if (( ${#missing[@]} )); then
        printf 'missing tools: %s\n' "${missing[*]}"; exit 1
    fi
    printf 'all required tools found\n'; exit 0
fi

if (( ${#missing[@]} )); then
    printf 'Missing tools: %s\n' "${missing[*]}" >&2
    exit 1
fi
if (( $# == 0 )) && [[ -e "$build_dir" ]]; then
    printf 'Default build directory already exists: %s\nPass two new paths for another fresh build.\n' "$build_dir" >&2
    exit 2
fi
if [[ "$build_dir" =~ [[:space:]] || "$destination" =~ [[:space:]] ]]; then
    printf 'Use folder paths without spaces.\n' >&2
    exit 2
fi
if [[ -e "$destination" ]]; then
    if (( replace_export )) && [[ -f $destination/SHA256SUMS ]]; then
        printf 'Removing the earlier export: %s\n' "$destination"
        rm -rf -- "$destination"
    elif (( replace_export )); then
        printf '%s exists but is not an earlier export (no SHA256SUMS); not deleting it.\n' "$destination" >&2
        exit 2
    else
        printf 'Export folder already exists: %s\nRemove it, pass a new path, or add --replace-export.\n' "$destination" >&2
        exit 2
    fi
fi

export LOCALVERSION=
unset LIBCLANG_PATH ANDROID_BUILD_TOP LINUX_BUILD_TOP

work=$(mktemp -d "${TMPDIR:-/tmp}/spring-standalone-install.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
args=(-C "$source_dir" O="$build_dir" ARCH=arm64 LLVM=1 LLVM_IAS=1 TARGET_BOARD_PLATFORM=blair)
make "${args[@]}" spring_defconfig
make -j"$jobs" "${args[@]}" Image modules dtbs
make "${args[@]}" headers_install INSTALL_HDR_PATH="$build_dir/headers"
make -j"$jobs" "${args[@]}" INSTALL_MOD_PATH="$work/installed" INSTALL_MOD_STRIP=1 modules_install
python3 "$tools/export.py" "$build_dir" "$work/installed" - "$destination" --source "$source_dir"
(cd "$destination" && sha256sum --quiet -c SHA256SUMS)
