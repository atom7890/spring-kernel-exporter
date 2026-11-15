# spring-prebuilt-tools

Builds the Spring (SM6375) kernel, modules and DTBs and exports a prebuilt
folder for the ROM. Works on any x86_64 Linux machine.

## Requirements

- Android clang r487747c (clang 17.0.2). Put its `bin` on `PATH`, or set `CLANG_BIN`.
  Other clang versions are untested; the script warns about them.
- `make flex bison perl python3 openssl lz4 pahole depmod modinfo`
  plus the OpenSSL and libelf development headers.
  - Arch: `base-devel flex bison perl python openssl lz4 pahole kmod libelf`
  - Debian/Ubuntu: `build-essential flex bison perl python3 libssl-dev lz4 dwarves kmod libelf-dev`
- GNU coreutils (`realpath -m`, `sha256sum`, `mktemp`). macOS is not supported.
- About 15 GB of free disk space for a full build.

## Use

Run from the kernel folder, or set `KERNEL_SOURCE`:

    ./build-standalone.sh --check                 # show detected settings and missing tools
    ./build-standalone.sh BUILD_DIR EXPORT_DIR    # both must be new paths, no spaces

| Variable | Meaning | Default |
|---|---|---|
| `KERNEL_SOURCE` | Spring kernel checkout | current folder, or the first Spring kernel folder next to this one |
| `CLANG_BIN` | folder holding `clang` and `ld.lld` | whatever is on `PATH` |
| `JOBS` | parallel jobs | number of CPUs |
| `TMPDIR` | scratch location | `/tmp` |

With no arguments, the build goes to `<source>/out-spring-fresh` and the export
to `./spring-prebuilt-fresh`; the script refuses to reuse an existing build folder.

`export.py` checks the built modules against `manifests/`. If you add or remove
a kernel module, update the matching `*.list` and `*.modules.load` files.
