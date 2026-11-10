#!/usr/bin/env python3
"""Export installed Spring build artifacts into a new prebuilt directory."""

import argparse
import hashlib
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

MANIFESTS = Path(__file__).resolve().parent / "manifests"
PARTITIONS = ("vendor_boot", "vendor_dlkm", "system_dlkm")


def entries(path):
    return [line.split("#", 1)[0].strip() for line in path.read_text().splitlines()
            if line.split("#", 1)[0].strip()]


def run(*args):
    return subprocess.check_output([str(arg) for arg in args], text=True).strip()


def module_name(name):
    return Path(name).name.removesuffix(".ko").replace("-", "_")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path, help="kernel output directory")
    parser.add_argument("installed", type=Path, help="INSTALL_MOD_PATH used by modules_install")
    parser.add_argument("mkdtboimg", help="host DTBO packer, or - for built-in packing")
    parser.add_argument("output", type=Path, help="new export directory")
    parser.add_argument("--source", type=Path, required=True, help="Spring kernel source checkout")
    args = parser.parse_args()

    build = args.build.resolve()
    output = args.output.resolve()
    release = (build / "include/config/kernel.release").read_text().strip()
    installed = args.installed.resolve() / "lib/modules" / release
    modules = {}
    for path in installed.rglob("*.ko"):
        if path.name in modules:
            parser.error(f"duplicate built module {path.name}")
        modules[path.name] = path

    groups = {part: entries(MANIFESTS / f"{part}.list") for part in PARTITIONS}
    vendor = set(groups["vendor_dlkm"])
    system = set(groups["system_dlkm"])
    if vendor & system or vendor | system != modules.keys():
        parser.error(f"vendor/system inventories differ from the build. "
                     f"Unassigned: {sorted(modules.keys() - vendor - system)}. "
                     f"Missing: {sorted((vendor | system) - modules.keys())}")
    for part in PARTITIONS:
        names = groups[part]
        if len(set(names)) != len(names) or not set(names) <= modules.keys():
            parser.error(f"invalid or missing entries in {part}.list")
        available = set(names) if part != "vendor_dlkm" else vendor | system
        providers = {module_name(name) for name in available}
        for name in names:
            src = modules[name]
            if run("modinfo", "-F", "vermagic", src).split()[0] != release:
                parser.error(f"{name} has stale vermagic")
            deps = set(filter(None, run("modinfo", "-F", "depends", src).split(",")))
            if missing := {module_name(dep) for dep in deps} - providers:
                parser.error(f"{part}/{name} has missing dependencies: {sorted(missing)}")
        for manifest in MANIFESTS.glob(f"{part}.modules.load*"):
            if missing := {module_name(name) for name in entries(manifest)} - providers:
                parser.error(f"{manifest.name} has missing modules: {sorted(missing)}")

    output.mkdir()
    images = output / "images"
    dtbs = images / "dtbs"
    dtbs.mkdir(parents=True)
    dts = build / "arch/arm64/boot/dts/vendor/qcom"
    shutil.copy2(build / "arch/arm64/boot/Image", images / "kernel")
    shutil.copy2(dts / "blair.dtb", dtbs)
    overlay = dts / "spring-sm6375-overlay.dtbo"
    if args.mkdtboimg == "-":
        blob = overlay.read_bytes()
        if len(blob) < 40 or struct.unpack_from(">II", blob) != (0xD00DFEED, len(blob)):
            parser.error(f"invalid overlay {overlay}")
        page = 4096
        size = 64 + len(blob)
        header = struct.pack(">8I", 0xD7B7AB1E, size, 32, 32, 1, 32, page, 0)
        entry = struct.pack(">8I", len(blob), 64, 0, 0, 0, 0, 0, 0)
        (images / "dtbo.img").write_bytes(header + entry + blob + bytes(-size % page))
    else:
        run(Path(args.mkdtboimg).resolve(), "create", images / "dtbo.img",
            "--page_size=4096", overlay)
    headers = output / "kernel-headers"
    shutil.copytree(build / "headers/include", headers)
    source = args.source.resolve()
    installer = source / "scripts/headers_install.sh"

    def install_uapi(header, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="spring-uapi-", dir="/tmp") as temp:
            exported = Path(temp) / header.name
            subprocess.run(["sh", installer, header, exported], cwd=build, check=True)
            shutil.copy2(exported, dest)

    vendor_uapi = (
        ("audio", "audio/audio-kernel/include/uapi"),
        ("display", "display/display-drivers/include/uapi"),
        ("linux", "dataipa/dataipa/drivers/platform/msm/include/uapi"),
    )
    for subsystem, tree in vendor_uapi:
        uapi = source / "techpack" / tree
        for header in sorted((uapi / subsystem).rglob("*.h")):
            relative = header.relative_to(uapi)
            dest = headers / relative
            install_uapi(header, dest)
            if subsystem == "audio":
                alias = headers.joinpath(*relative.parts[1:])
                if not alias.exists():
                    alias.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest, alias)

    extra_uapi = {
        "linux/nfc/nfcinfo.h": "nfc/nxp/opensource/driver/include/uapi/linux/nfc/nfcinfo.h",
        "linux/qseecom.h": "securemsm/securemsm-kernel/include/uapi/linux/qseecom.h",
        "linux/qti-smmu-proxy.h": "securemsm/securemsm-kernel/smmu-proxy/include/uapi/linux/qti-smmu-proxy.h",
        "linux/ubwcp_ioctl.h": "multimedia/mm-sys-kernel/ubwcp/include/uapi/ubwcp_ioctl.h",
    }
    for relative, header in extra_uapi.items():
        install_uapi(source / "techpack" / header, headers / relative)

    compat = MANIFESTS.parent / "compat-headers"
    for header in sorted(compat.rglob("*.h")):
        dest = headers / header.relative_to(compat)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(header, dest)

    header_hash = hashlib.sha256()
    for header in sorted(headers.rglob("*.h")):
        header_hash.update(str(header.relative_to(headers)).encode() + b"\0")
        header_hash.update(hashlib.sha256(header.read_bytes()).digest())
    (headers / "Makefile").write_text(
        f"# Exported header SHA-256: {header_hash.hexdigest()}\n"
        "VERSION = 6\nPATCHLEVEL = 1\n\n"
        ".PHONY: all headers_install\n"
        "all:\n\t@true\n\n"
        "headers_install:\n"
        "\t@mkdir -p \"$(O)/usr/include\"\n"
        "\t@cp -R -- $(wildcard */) \"$(O)/usr/include/\"\n"
    )

    system_root = output / "modules/system_dlkm"
    system_release = system_root / release
    signed = {}
    for name in groups["system_dlkm"]:
        src = modules[name]
        dst = system_release / src.relative_to(installed)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if not run("modinfo", "-F", "signer", dst):
            run(build / "scripts/sign-file", "sha256", build / "certs/signing_key.pem",
                build / "certs/signing_key.x509", dst)
        signed[name] = dst

    system_load = entries(MANIFESTS / "system_dlkm.modules.load")
    (system_release / "modules.load").write_text(
        "".join(f"{signed[Path(path).name].relative_to(system_release)}\n"
                for path in system_load)
    )

    # depmod expects a lib/modules/<release> hierarchy.
    depmod_root = output / ".depmod"
    depmod_modules = depmod_root / "lib/modules"
    depmod_modules.mkdir(parents=True)
    (depmod_modules / release).symlink_to(system_release, target_is_directory=True)
    shutil.copy2(installed / "modules.builtin", system_release)
    shutil.copy2(installed / "modules.builtin.modinfo", system_release)
    order = entries(installed / "modules.order")
    (system_release / "modules.order").write_text(
        "".join(f"{path}\n" for path in order if Path(path).name in system)
    )
    try:
        run("depmod", "-b", depmod_root, release)
    finally:
        shutil.rmtree(depmod_root)

    for part in ("vendor_boot", "vendor_dlkm"):
        dst = output / "modules" / part
        dst.mkdir()
        for name in groups[part]:
            shutil.copy2(signed.get(name, modules[name]), dst / name)
        for manifest in MANIFESTS.glob(f"{part}.modules.*"):
            shutil.copy2(manifest, dst / manifest.name.removeprefix(f"{part}."))

    (output / "kernel.release").write_text(release + "\n")
    files = sorted(path for path in output.rglob("*") if path.is_file())
    (output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output)}\n"
        for path in files
    ))
    print(f"Exported {release} to {output}")


if __name__ == "__main__":
    main()
