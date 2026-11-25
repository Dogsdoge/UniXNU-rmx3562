#!/usr/bin/env python3
"""Pack kernel.macho + PEARL_UEFI.fd + mt6895.dtb into a boot_a image for RMX3562.

Adapted from scripts/build-bootimg.py (raphael).  The layout is unchanged:

    kernel  = gzip(BootShim + UEFI FD) + DTB
    ramdisk = payload.img

and the packing rules are the MT6895 ones documented in rmx3562_boot.py:
v4 header, 4096-byte pages, EMPTY cmdline, os_version 0x18000162, <= 64 MiB.

Differences from the raphael template:

* It compiles the EDK2 fork's BootShim.S (UEFI_BASE/UEFI_SIZE), because the FD
  is PEARL_UEFI.fd, not a Mu-Silicium volume; the framework's bootimg.settings()
  reads Mu-Silicium Resources/Configs and does not apply here.
* It does not call the framework's compile_shim(): that validates the Linux
  image header at +8/+16, while the EDK2 shim stores base/size at +72/+80.
* It writes the v4 header itself.  EDK2 Tools/mkbootimg.py always sets
  ramdisk_size=0, and the framework mkbootimg needs a Mu-Silicium checkout, so
  neither can carry the payload ramdisk.
* It does not patch /chosen/linux,initrd-start: v4 has no ramdisk load address
  (the raphael patch keys off abl_base+ramdisk_offset), and the mt6895 DTB must
  stay byte-for-byte as dumped.

Usage:
    scripts/build-rmx3562-bootimg.py \
        --kernel kernel.macho \
        --fd PEARL_UEFI.fd \
        --dtb /media/sd/anew/ios/dump/mt6895.dtb \
        -o out/rmx3562/boot/boot-rmx3562.img

If --ramdisk is omitted and no payload exists yet, the payload is built from
--kernel with build-rmx3562-payload.py (needs mtools).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rmx3562_boot import (  # noqa: E402
    BOOT_PART_SIZE, DTB_LIMIT, FDT_MAGIC, OS_VERSION, PAGE, UEFI_BASE, UEFI_SIZE_MAX,
    compile_bootshim, find_bootshim, gzip_kernel, pack_boot_v4, read_header,
    sha256_file, verify_boot_image,
)

ROOT = HERE.parent
DEFAULT_PAYLOAD = ROOT / "out" / "rmx3562" / "payload" / "payload.img"


def number(text):
    return int(text, 0)


def build_payload(args, output):
    """Run build-rmx3562-payload.py, which turns kernel.macho into a FAT volume."""
    command = [sys.executable, str(HERE / "build-rmx3562-payload.py"),
               "--kernel", str(args.kernel),
               "--payload-out", str(output),
               "--size-mib", str(args.payload_size_mib)]
    if args.adt:
        command += ["--adt", str(args.adt)]
    if args.trust_cache:
        command += ["--trust-cache", str(args.trust_cache)]
    if args.unixnu:
        command += ["--unixnu", str(args.unixnu)]
    for item in args.kernel_arg:
        command += ["--kernel-arg", item]
    print("bootimg: building payload:", " ".join(command))
    result = subprocess.run(command)
    if result.returncode != 0:
        raise SystemExit(f"payload build failed (exit {result.returncode}); see the message above")
    if not output.is_file():
        raise SystemExit(f"payload build did not produce {output}")
    return output


def resolve_ramdisk(args):
    if args.ramdisk:
        ramdisk = Path(args.ramdisk).expanduser()
        if not ramdisk.is_file():
            raise SystemExit(f"ramdisk not found: {ramdisk}")
        return ramdisk
    if args.kernel:
        # An explicit kernel artifact is the source of truth: rebuild the payload
        # from it rather than silently reusing an older payload.img from out/.
        return build_payload(args, DEFAULT_PAYLOAD)
    if DEFAULT_PAYLOAD.is_file():
        print(f"bootimg: using existing payload {DEFAULT_PAYLOAD}")
        return DEFAULT_PAYLOAD
    raise SystemExit(
        "no ramdisk: pass --ramdisk payload.img, or --kernel kernel.macho so the "
        "payload can be built"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kernel", help="prelinked kernel.macho; used to build the ramdisk payload")
    parser.add_argument("--fd", required=True, help="EDK2 firmware volume (CI rmx3562-uefi PEARL_UEFI.fd)")
    parser.add_argument("--dtb", required=True, help="MT6895 device tree (DTO-applied /proc dump; <= 1 MiB)")
    parser.add_argument("--ramdisk", help="prebuilt payload.img (default: out/rmx3562/payload/payload.img)")
    parser.add_argument("--adt", help="ADT sidecar for the payload (default: out/rmx3562.adt / mkadt.py)")
    parser.add_argument("--trust-cache", help="optional trust cache for the payload")
    parser.add_argument("--kernel-arg", action="append", default=[], metavar="NAME[=VALUE]",
                        help="override/add a payload kernel argument (repeatable)")
    parser.add_argument("--payload-size-mib", type=int, default=48,
                        help="payload FAT size when it is built (default 48; boot_a is 64 MiB)")
    parser.add_argument("--uefi-base", type=number, default=UEFI_BASE,
                        help=f"FD load address (default {UEFI_BASE:#x}, pearl.fdf FD_BASE)")
    parser.add_argument("--uefi-size", type=number, default=0,
                        help="FD copy size (default: the FD file length; must match exactly)")
    parser.add_argument("--os-version", type=number, default=OS_VERSION,
                        help=f"v4 os_version field (default {OS_VERSION:#x})")
    parser.add_argument("--pagesize", type=number, default=PAGE)
    parser.add_argument("--part-size", type=number, default=BOOT_PART_SIZE,
                        help=f"boot_a size in bytes (default {BOOT_PART_SIZE:#x} = 64 MiB)")
    parser.add_argument("--bootshim", help="BootShim.S path (default: found next to --fd / --edk2)")
    parser.add_argument("--edk2", help="EDK2-MT6895 checkout (when --fd is outside it)")
    parser.add_argument("--unixnu", help="UniXNU framework checkout for the payload FAT writer")
    parser.add_argument("-o", "--output", default=str(ROOT / "out" / "rmx3562" / "boot" / "boot-rmx3562.img"))
    parser.add_argument("--dry-run", action="store_true", help="resolve and print the plan, write nothing")
    args = parser.parse_args()

    fd_path = Path(args.fd).expanduser()
    if not fd_path.is_file():
        raise SystemExit(f"firmware volume not found: {fd_path} (download the rmx3562-uefi artifact)")
    firmware = fd_path.read_bytes()
    size = args.uefi_size or len(firmware)
    if size != len(firmware):
        raise SystemExit(
            f"--uefi-size is {size:#x} but {fd_path} is {len(firmware):#x} bytes; BootShim copies "
            "exactly the size it was compiled with, so the two must match"
        )
    if len(firmware) > UEFI_SIZE_MAX:
        raise SystemExit(f"firmware volume is {len(firmware):#x} bytes; the pearl FD maximum is {UEFI_SIZE_MAX:#x}")

    dtb_path = Path(args.dtb).expanduser()
    if not dtb_path.is_file():
        raise SystemExit(f"device tree not found: {dtb_path}")
    dtb = dtb_path.read_bytes()
    if dtb[:4] != FDT_MAGIC:
        raise SystemExit(f"{dtb_path}: not a flattened device tree (magic 0xd00dfeed)")
    if len(dtb) > DTB_LIMIT:
        raise SystemExit(
            f"device tree is {len(dtb)} bytes; BootShim silently skips anything above "
            f"{DTB_LIMIT} bytes, so the kernel would boot without a DT"
        )

    bootshim = find_bootshim(args.bootshim, args.edk2, fd_path)
    output = Path(args.output).expanduser()

    if args.dry_run:
        plan = {
            "kernel": str(args.kernel) if args.kernel else None,
            "fd": str(fd_path), "fd_bytes": len(firmware),
            "dtb": str(dtb_path), "dtb_bytes": len(dtb),
            "bootshim": str(bootshim), "uefi_base": hex(args.uefi_base),
            "uefi_size": hex(size), "os_version": hex(args.os_version),
            "pagesize": args.pagesize, "part_size": args.part_size,
            "ramdisk": args.ramdisk or str(DEFAULT_PAYLOAD),
            "output": str(output), "cmdline": "",
        }
        print(json.dumps(plan, indent=2))
        return

    ramdisk_path = resolve_ramdisk(args)
    ramdisk = ramdisk_path.read_bytes()

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rmx3562-bootimg-") as scratch_name:
        shim, compiler = compile_bootshim(bootshim, args.uefi_base, size, Path(scratch_name))
        kernel = gzip_kernel(shim, firmware, dtb)
        image_bytes = pack_boot_v4(
            kernel, ramdisk, output,
            os_version=args.os_version, pagesize=args.pagesize,
            part_size=args.part_size, cmdline="",
        )

    info, decompressed = verify_boot_image(
        output, kernel_blob=kernel, ramdisk=ramdisk, dtb=dtb,
        os_version=args.os_version, pagesize=args.pagesize,
    )
    if decompressed != shim + firmware:
        raise SystemExit(f"{output}: decompressed kernel is not BootShim + firmware volume")

    report = {
        "image": str(output),
        "image_bytes": image_bytes,
        "image_sha256": sha256_file(output),
        "header_version": info["header_version"],
        "header_size": info["header_size"],
        "os_version": f"{info['os_version']:#010x}",
        "cmdline": "",
        "pagesize": args.pagesize,
        "kernel_bytes": len(kernel),
        "ramdisk_bytes": len(ramdisk),
        "ramdisk_sha256": sha256_file(ramdisk_path),
        "dtb_bytes": len(dtb),
        "dtb_sha256": sha256_file(dtb_path),
        "fd_bytes": len(firmware),
        "fd_sha256": sha256_file(fd_path),
        "uefi_base": f"{args.uefi_base:#x}",
        "uefi_size": f"{size:#x}",
        "bootshim": str(bootshim),
        "bootshim_bytes": len(shim),
        "bootshim_compiler": compiler,
        "boot_part_size": args.part_size,
        "hardware_tested": False,
    }
    (output.parent / "boot.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"bootimg: {output} ({image_bytes} bytes)")
    print(f"  kernel={len(kernel)} ramdisk={len(ramdisk)} dtb={len(dtb)} "
          f"os_version={info['os_version']:#x} cmdline=<empty>")


if __name__ == "__main__":
    main()
