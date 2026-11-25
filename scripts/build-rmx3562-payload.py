#!/usr/bin/env python3
"""Build the RMX3562 FAT payload (the boot image ramdisk) from kernel.macho.

Adapted from scripts/build-payload.py.  The raphael version calls
``unixnu.payload.build(ctx)``; this device cannot use that entry point as-is:

* board.toml is ``schema_version = 2``, so the framework derives the ADT by
  compiling ``dts/rmx3562.dts``.  That file includes ``xaga-camera.dtsi`` and
  ``<dt-bindings/...>``, which are not in the device tree's include_dirs, so
  ``dtc`` fails.  This repository already ships the working generator
  (``scripts/mkadt.py --device rmx3562``, the one the CI validate job runs).
* build-payload.py's hand-built ``types.SimpleNamespace`` ctx no longer has the
  fields the current framework reads (schema_version/config/device/...).

So this script keeps the template's structure -- stage
``EFI/UniXNU/{Payloads/kernel.macho,Payloads/kernel.macho.adt,unixnu.toml}``
then turn the directory into a FAT volume -- and reuses the framework's own
``unixnu.payload.write_volume`` (mtools) for the last step when a framework
checkout is available.  The fallback runs the same mformat/mcopy commands.

Usage:
    scripts/build-rmx3562-payload.py --kernel kernel.macho [--adt rmx3562.adt]
                                     [--trust-cache FILE] [--size-mib 48]
                                     [--payload-out out/rmx3562/payload/payload.img]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rmx3562_boot import sha256_file  # noqa: E402

ROOT = HERE.parent
EPOCH = 315532800   # 1980-01-01, same as unixnu.payload; keeps the volume reproducible
ARG_RE = re.compile(r"[A-Za-z0-9_.\-]+(=[A-Za-z0-9_.,/\-:=]+)?")


def find_framework(explicit):
    """Find the UniXNU framework checkout (sibling 'unixnu' in CI, or --unixnu)."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("UNIXNU_DIR"):
        candidates.append(Path(os.environ["UNIXNU_DIR"]))
    candidates += [ROOT / "unixnu", ROOT.parent / "unixnu"]
    for candidate in candidates:
        if (candidate / "scripts" / "unixnu" / "payload.py").is_file():
            return candidate.resolve()
    return None


def load_board():
    return tomllib.loads((ROOT / "board.toml").read_text())


def kernel_arguments(spec, extras):
    """Board [payload] kernel_args with NAME=VALUE extras replacing same-name items."""
    names = {item.split("=", 1)[0] for item in extras}
    values = list(extras) + [item for item in spec.get("kernel_args", []) if item.split("=", 1)[0] not in names]
    for value in values:
        if not ARG_RE.fullmatch(value):
            raise SystemExit(f"unsupported kernel argument: {value!r}")
    line = " ".join(values)
    if len(line) > 1000:
        raise SystemExit(f"kernel command line is {len(line)} bytes; the loader limit is 1000")
    return line


def generate_adt(destination):
    """Run the device's ADT generator, mirroring the CI validate step."""
    command = [sys.executable, str(ROOT / "scripts" / "mkadt.py"), "--device", "rmx3562", "--dump"]
    extents = ROOT / "inputs" / "root-extents.txt"
    if extents.is_file():
        blocks = 0
        for line in extents.read_text().splitlines():
            fields = line.split()
            if fields and fields[0][:1].isdigit():
                blocks += int(fields[2])
        command += ["--root-extents", str(extents), "--root-blocks", str(blocks)]
    command.append(str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    print("payload: generating ADT with", " ".join(command))
    subprocess.run(command, check=True)


def write_toml(stage, spec, line):
    """The same unixnu.toml unixnu.payload.build writes for a firmware loader."""
    config = (
        "version = 1\n[boot]\n"
        f'timeout = {spec.get("timeout", 3)}\ndefault = "xnu"\n[kits]\nautoload = false\n'
        f'[[entry]]\nid = "xnu"\ntitle = {json.dumps(spec.get("title", "UniXNU"))}\n'
        'type = "xnu"\npath = "Payloads/kernel.macho"\n'
        f'args = "{line}"\n'
        '[[entry]]\nid = "reset"\ntitle = "Restart"\ntype = "action"\naction = "reset"\n'
        '[[entry]]\nid = "shutdown"\ntitle = "Power off"\ntype = "action"\naction = "shutdown"\n'
    )
    (stage / "EFI/UniXNU/unixnu.toml").write_text(config)


def wipe_metadata(stage):
    for path in [stage, *stage.rglob("*")]:
        os.utime(path, (EPOCH, EPOCH))


def write_volume(stage, image, label, size_mib, framework):
    """Populate a FAT volume; reuse the framework's mtools writer when possible."""
    if framework is not None:
        sys.path.insert(0, str(framework / "scripts"))
        try:
            from unixnu.payload import write_volume as framework_write_volume
        except Exception as error:      # fall through to the local copy
            print(f"payload: framework write_volume unavailable ({error}); using mtools directly")
        else:
            try:
                framework_write_volume(stage, image, label, size_mib)
            except Exception as error:
                raise SystemExit(f"payload: {error}") from error
            return

    for program in ("mformat", "mcopy"):
        if not shutil.which(program):
            raise SystemExit(f"{program} is missing: install mtools (the UniXNU Linux toolchain does)")
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1", TZ="UTC")
    image.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".payload-", dir=image.parent) as scratch:
        candidate = Path(scratch) / image.name
        subprocess.run(["mformat", "-C", "-F", "-T", str(size_mib * 2048), "-v", label,
                        "-N", "53484e47", "-i", str(candidate), "::"], check=True, env=env)
        for entry in sorted(stage.iterdir()):
            subprocess.run(["mcopy", "-i", str(candidate), "-s", "-Q", "-m", str(entry), "::/"],
                           check=True, env=env)
        candidate.replace(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kernel", required=True, help="prelinked kernel.macho (CI rmx3562-kernel artifact)")
    parser.add_argument("--adt", help="ADT sidecar (default: out/rmx3562.adt, else generate with scripts/mkadt.py)")
    parser.add_argument("--trust-cache", help="optional trust cache bundle copied next to the kernel")
    parser.add_argument("--kernel-arg", action="append", default=[], metavar="NAME[=VALUE]",
                        help="override/add a board kernel argument (repeatable)")
    parser.add_argument("--size-mib", type=int, help="FAT volume size (default: board.toml [payload] size_mib)")
    parser.add_argument("--volume", help="FAT volume label (default: board.toml [payload] volume)")
    parser.add_argument("--payload-out", default=str(ROOT / "out" / "rmx3562" / "payload" / "payload.img"))
    parser.add_argument("--unixnu", help="UniXNU framework checkout (default: sibling unixnu/)")
    args = parser.parse_args()

    kernel = Path(args.kernel).expanduser()
    if not kernel.is_file():
        raise SystemExit(f"kernel.macho not found: {kernel} (download the rmx3562-kernel artifact)")

    board = load_board()
    spec = board.get("payload")
    if not spec:
        raise SystemExit("board.toml has no [payload]")
    line = kernel_arguments(spec, args.kernel_arg)

    adt = Path(args.adt).expanduser() if args.adt else ROOT / "out" / "rmx3562.adt"
    if not adt.is_file():
        generate_adt(adt)
    if not adt.is_file() or adt.stat().st_size == 0:
        raise SystemExit(f"ADT missing or empty: {adt}")

    if args.payload_out:
        image = Path(args.payload_out).expanduser()
    size_mib = args.size_mib if args.size_mib is not None else int(spec.get("size_mib", 48))
    if size_mib < 1:
        raise SystemExit("--size-mib must be positive")
    volume = (args.volume or spec.get("volume", "RMX3562"))[:11]

    framework = find_framework(args.unixnu)
    with tempfile.TemporaryDirectory(prefix="rmx3562-payload-") as scratch_name:
        stage = Path(scratch_name) / "stage"
        payloads = stage / "EFI" / "UniXNU" / "Payloads"
        payloads.mkdir(parents=True)
        shutil.copyfile(kernel, payloads / "kernel.macho")
        shutil.copyfile(adt, payloads / "kernel.macho.adt")
        if args.trust_cache:
            trust = Path(args.trust_cache).expanduser()
            if not trust.is_file():
                raise SystemExit(f"trust cache not found: {trust}")
            shutil.copyfile(trust, payloads / "kernel.macho.trustcache")
        write_toml(stage, spec, line)
        content = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
        if content > size_mib * 1024 * 1024:
            raise SystemExit(f"payload files take {content >> 20} MiB; raise --size-mib (now {size_mib})")
        wipe_metadata(stage)
        write_volume(stage, image, volume, size_mib, framework)

    report = {
        "kernel_sha256": sha256_file(kernel),
        "adt_sha256": sha256_file(adt),
        "payload_sha256": sha256_file(image),
        "payload_bytes": image.stat().st_size,
        "payload_mib": size_mib,
        "volume": volume,
        "kernel_args": line,
        "framework": str(framework) if framework else None,
        "hardware_tested": False,
    }
    (image.parent / "payload.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"payload: {image} ({image.stat().st_size} bytes, {size_mib} MiB)")


if __name__ == "__main__":
    main()
