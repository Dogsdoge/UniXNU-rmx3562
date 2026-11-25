"""RMX3562 / MT6895 (realme GT Neo3, Dimensity 8100) boot-image primitives.

The raphael device in this repository packs an Android boot image as

    kernel  = gzip(BootShim + UEFI FD) + DTB
    ramdisk = payload.img   (the FAT volume UniXNU boots from)

and the same shape is what MT6895 LK expects.  What changes is the hardware
contract, so those values live here instead of being copied from raphael:

* header_version 4, pagesize 4096, 1584-byte header  -- CONFIRMED: live boot_a
  header, offset +20 is 1584 and +40 is 4.
* os_version 0x18000162                              -- CONFIRMED: live boot_a
  v4 header offset +16 (anti-rollback must match the stock image).
* cmdline empty                                      -- CONFIRMED: live boot_a
  offset +44 is all zeroes.  bootopt=... comes from vendor_boot_a and MUST NOT
  be repeated here (a wrong one fails DDR init in preloader).
* boot partition 64 MiB = 0x4000000                   -- CONFIRMED: boot_a spans
  LBA 257536..273919 on 4096-byte UFS sectors (16384 * 4096 = 67,108,864).
* UEFI FD base 0x50000000                             -- CONFIRMED for the
  PEARL_UEFI.fd this device builds: pearl.fdf FD_BASE, PlatformBuild.py
  BLD_*_FD_BASE and build-pearl.sh UEFI_BASE all agree.  (BOOT-IMAGE.md's
  0x41000000 belongs to the separate 2 MiB SILICIUM_UEFI.fd, not this FD.)
* UEFI FD maximum 0x700000                            -- CONFIRMED: pearl.fdf
  FD_SIZE.  The actual length is taken from the file, because BootShim copies
  exactly UEFI_SIZE bytes and must not read past the appended volume.
* DTB <= 1 MiB                                        -- CONFIRMED: BootShim's
  DTB_COPY_SIZE.  A larger DTB is silently skipped by BootShim, so refuse it.

This module deliberately does not import ``unixnu`` so the packer can run in a
job that only has the EDK2 fork and the artifacts.
"""
from __future__ import annotations

import gzip
import hashlib
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

PAGE = 4096
HEADER_VERSION = 4
HEADER_SIZE = 1584          # boot_img_hdr_v4, signature_size included
CMDLINE_SIZE = 1536         # BOOT_ARGS_SIZE + BOOT_EXTRA_ARGS_SIZE
SIGNATURE_SIZE = 0         # EDK2 Tools/mkbootimg.py leaves this zero; matching it
OS_VERSION = 0x18000162
BOOT_PART_SIZE = 0x4000000  # 64 MiB
UEFI_BASE = 0x50000000
UEFI_SIZE_MAX = 0x700000
DTB_LIMIT = 0x100000        # BootShim DTB_COPY_SIZE
FDT_MAGIC = b"\xd0\x0d\xfe\xed"


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pad(data: bytes, size: int = PAGE) -> bytes:
    remainder = len(data) % size
    return data if remainder == 0 else data + b"\0" * (size - remainder)


def _find_program(*names):
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_bootshim(source=None, edk2=None, fd=None):
    """Locate BootShim.S: explicit file, then <edk2>/BootShim, then next to --fd."""
    if source:
        path = Path(source)
        if not path.is_file():
            raise SystemExit(f"BootShim source not found: {path}")
        return path
    roots = []
    if edk2:
        roots.append(Path(edk2))
    if fd:
        parents = Path(fd).resolve().parents
        roots.extend(parents[:8])   # Build/<target>_CLANG*/FV/PEARL_UEFI.fd -> checkout root
    for root in roots:
        candidate = root / "BootShim" / "BootShim.S"
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "cannot find BootShim/BootShim.S: pass --bootshim FILE, --edk2 DIR, "
        "or an --fd inside the EDK2 checkout"
    )


def compile_bootshim(bootshim, uefi_base, uefi_size, scratch):
    """Compile the EDK2 BootShim and return (bytes, description).

    The EDK2 shim is not the Mu-Silicium one: it carries the load address and
    size at +72/+80, not the Linux image header at +8/+16, so the framework's
    compile_shim() cannot validate it.  Compile it with the same flags
    build-pearl.sh uses and prove _Payload sits exactly at the end.
    """
    source = Path(bootshim)
    scratch = Path(scratch)
    flags = [f"-DUEFI_BASE={uefi_base:#x}", f"-DUEFI_SIZE={uefi_size:#x}"]

    gcc = _find_program("aarch64-linux-gnu-gcc")
    if gcc:
        compiler, objcopy, nm = gcc, _find_program("aarch64-linux-gnu-objcopy", "objcopy"), _find_program("aarch64-linux-gnu-nm", "nm")
        compile_cmd = [compiler, "-c", str(source), "-o", str(scratch / "shim.o"), *flags]
        description = "aarch64-linux-gnu-gcc"
    else:
        compiler = _find_program("clang")
        objcopy = _find_program("llvm-objcopy", "llvm-objcopy-18")
        nm = _find_program("llvm-nm", "llvm-nm-18")
        if not compiler:
            raise SystemExit(
                "no AArch64 compiler: install gcc-aarch64-linux-gnu (or clang/llvm)"
            )
        compile_cmd = [compiler, "--target=aarch64-none-elf", "-c", str(source), "-o", str(scratch / "shim.o"), *flags]
        description = "clang --target=aarch64-none-elf"
    if not objcopy or not nm:
        raise SystemExit("objcopy/nm missing for the selected BootShim compiler")

    try:
        subprocess.run(compile_cmd, check=True)
        obj = scratch / "shim.o"
        subprocess.run([objcopy, "-O", "binary", str(obj), str(scratch / "shim.bin")], check=True)
        shim = (scratch / "shim.bin").read_bytes()
        symbols = subprocess.run([nm, "-n", str(obj)], check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"BootShim build failed ({description}): {error}") from error
    offset = next((int(line.split()[0], 16) for line in symbols.splitlines() if line.endswith(" _Payload")), None)
    if offset != len(shim):
        raise SystemExit(
            f"BootShim: _Payload is at {offset:#x} but the binary is {len(shim):#x} bytes; "
            "the firmware volume would not start on a 16-byte boundary after it"
        )
    if shim[56:60] != b"ARM\x64":
        raise SystemExit("BootShim: ARM64 image magic is not at offset 56; wrong source file")
    return shim, description


def pack_boot_v4(kernel, ramdisk, output, *, os_version=OS_VERSION, pagesize=PAGE,
                 part_size=BOOT_PART_SIZE, cmdline=""):
    """Write an Android boot v4 image with kernel + ramdisk and an empty cmdline."""
    if cmdline:
        raise SystemExit(
            "refusing to write a boot cmdline: bootopt comes from vendor_boot_a and a "
            "wrong value here hard-bricks the device at DDR init"
        )
    if pagesize != PAGE:
        raise SystemExit(f"MT6895 LK uses a {PAGE}-byte boot page, not {pagesize}")
    if not kernel:
        raise SystemExit("kernel payload is empty")
    if len(kernel) > 0xFFFFFFFF or len(ramdisk) > 0xFFFFFFFF:
        raise SystemExit("kernel or ramdisk does not fit a 32-bit boot header field")

    header = bytearray(HEADER_SIZE)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<IIII", header, 8, len(kernel), len(ramdisk),
                     os_version & 0xFFFFFFFF, HEADER_SIZE)
    struct.pack_into("<I", header, 40, HEADER_VERSION)
    # +44 .. +1579 is the 1536-byte cmdline: leave it zero (empty).
    struct.pack_into("<I", header, 1580, SIGNATURE_SIZE)

    image = bytearray()
    image += pad(bytes(header), pagesize)
    image += pad(kernel, pagesize)
    if ramdisk:
        image += pad(ramdisk, pagesize)
    if len(image) > part_size:
        raise SystemExit(
            f"boot image would be {len(image)} bytes, larger than boot_a ({part_size} bytes); "
            "shrink --size-mib / --payload-size-mib"
        )
    if part_size:
        image += b"\0" * (part_size - len(image))

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".boot-", dir=output.parent, delete=False) as handle:
        pending = Path(handle.name)
        handle.write(image)
    try:
        pending.replace(output)       # publish only a complete image
    finally:
        pending.unlink(missing_ok=True)
    output.chmod(0o644)
    return len(image)


def read_header(path):
    """Return the v4 header fields of a boot image (enough to prove compatibility)."""
    data = Path(path).read_bytes()
    if data[:8] != b"ANDROID!":
        raise SystemExit(f"{path}: not an Android boot image")
    kernel_size, ramdisk_size, os_version, header_size = struct.unpack_from("<4I", data, 8)
    header_version = struct.unpack_from("<I", data, 40)[0]
    cmdline = data[44:44 + CMDLINE_SIZE]
    signature_size = struct.unpack_from("<I", data, 1580)[0] if len(data) >= HEADER_SIZE else None
    return {
        "kernel_size": kernel_size,
        "ramdisk_size": ramdisk_size,
        "os_version": os_version,
        "header_size": header_size,
        "header_version": header_version,
        "cmdline": cmdline,
        "signature_size": signature_size,
        "bytes": len(data),
    }


def verify_boot_image(path, *, kernel_blob, ramdisk, dtb, os_version=OS_VERSION, pagesize=PAGE):
    """Unpack the image and prove every part round-trips; raise SystemExit on mismatch."""
    info = read_header(path)
    if info["header_version"] != HEADER_VERSION or info["header_size"] != HEADER_SIZE:
        raise SystemExit(f"{path}: header is v{info['header_version']}/{info['header_size']}, expected v4/1584")
    if info["os_version"] != os_version:
        raise SystemExit(f"{path}: os_version is {info['os_version']:#x}, expected {os_version:#x}")
    if any(info["cmdline"]):
        raise SystemExit(f"{path}: boot cmdline is not empty; bootopt must stay in vendor_boot_a")
    if info["signature_size"]:
        raise SystemExit(f"{path}: unexpected v4 signature_size {info['signature_size']}")
    if info["kernel_size"] != len(kernel_blob) or info["ramdisk_size"] != len(ramdisk):
        raise SystemExit(f"{path}: header sizes do not match the inputs")

    data = Path(path).read_bytes()
    start = pagesize
    kernel = data[start:start + info["kernel_size"]]
    if kernel != kernel_blob:
        raise SystemExit(f"{path}: kernel bytes differ from the packed payload")
    ramdisk_start = start + len(pad(kernel_blob, pagesize))
    if ramdisk and data[ramdisk_start:ramdisk_start + len(ramdisk)] != ramdisk:
        raise SystemExit(f"{path}: ramdisk bytes differ from the packed payload")

    # The DTB is appended after the gzip stream (Image.gz-dtb convention): the
    # decompressor stops at the end of the gzip member, the rest must be the DTB.
    unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
    firmware = unpacker.decompress(kernel)
    if not unpacker.eof or unpacker.unused_data != dtb:
        raise SystemExit(f"{path}: gzip(BootShim+FD) and appended DTB do not round-trip")
    return info, firmware


def gzip_kernel(shim, firmware, dtb):
    """kernel = gzip(BootShim + UEFI FD) + DTB, deterministic (mtime=0)."""
    compressed = gzip.compress(shim + firmware, mtime=0)
    return compressed + dtb
