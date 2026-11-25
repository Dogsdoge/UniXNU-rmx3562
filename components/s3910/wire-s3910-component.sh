#!/usr/bin/env bash
# wire-s3910-component.sh — register the S3910 touch component in a UniXNU checkout.
#
# The framework does not know this component: its board.toml entry would be an
# "unknown component" at defconfig. This script appends the catalog entry,
# links the component directory into the framework's drivers/ (feeds.resolve
# then finds it as a base module, so no feeds.toml source or pin is needed) and
# copies the build recipe into the framework scripts/ directory that
# tree.assemble copies to tree/scripts/. Rerunning it is a no-op.
#
# Usage: bash wire-s3910-component.sh <unixnu framework dir> <device repo dir>
set -euo pipefail

UNIXNU_DIR="${1:?usage: wire-s3910-component.sh <unixnu dir> <device dir>}"
DEVICE_DIR="${2:?usage: wire-s3910-component.sh <unixnu dir> <device dir>}"
# The symlink target must be absolute: a relative DEVICE_DIR would resolve
# against <unixnu>/drivers/ and leave a broken link.
UNIXNU_DIR="$(cd "$UNIXNU_DIR" && pwd -P)"
DEVICE_DIR="$(cd "$DEVICE_DIR" && pwd -P)"
[ -f "$UNIXNU_DIR/components.toml" ] || { echo "no components.toml in $UNIXNU_DIR" >&2; exit 1; }
[ -f "$DEVICE_DIR/components/s3910/build-s3910.py" ] || { echo "no build-s3910.py in $DEVICE_DIR/components/s3910" >&2; exit 1; }

python3 - "$UNIXNU_DIR" <<'PY'
import pathlib
import sys

catalog = pathlib.Path(sys.argv[1]) / 'components.toml'
text = catalog.read_text()
if 'id = "S3910"' in text:
    print('S3910 already registered (skip)')
else:
    entry = '''
[[component]]
id = "S3910"
kind = "driver"
function = "Synaptics S3910 touch driver on MT6895 SPI2, posting to /dev/redwood-touch"
scope = "board"
# The module comes from the symlink this script creates (a base module); the
# repository is documentation and the path is the fallback if it is missing.
repository = "https://github.com/Dogsdoge/UniXNU-rmx3562.git"
path = "drivers/S3910"
hardware = ["synaptics,s3910 on mediatek,mt6895 SPI2"]
os_abi = ["ios-16.5-20F66 (xnu-8796.121.2)"]
board_dependencies = ["S3910"]
verified_devices = []
dependencies = []
source_files = ["redwood_input.c"]
runtime_identifiers = ["redwood_s3910_init", "redwood_touch_post"]
build_stage = "kernel"
build = ["build-s3910.py"]
# Empty outputs: the files are compiled by the XNU build itself.
outputs = []
'''
    with catalog.open('a') as stream:
        stream.write(entry)
    print('added S3910 component to components.toml')
PY

target="$UNIXNU_DIR/drivers/S3910"
if [ -e "$target" ] || [ -L "$target" ]; then
  echo "$target already exists (skip)"
else
  mkdir -p "$UNIXNU_DIR/drivers"
  ln -s "$DEVICE_DIR/components/s3910" "$target"
  echo "linked $target -> $DEVICE_DIR/components/s3910"
fi

# tree.assemble copies <framework>/scripts into tree/scripts and preflight looks
# for the recipe there, so the build script must live in the framework's scripts
# directory. Refresh it when it differs (the component dir stays authoritative).
source_script="$DEVICE_DIR/components/s3910/build-s3910.py"
target_script="$UNIXNU_DIR/scripts/build-s3910.py"
if [ ! -f "$target_script" ] || ! cmp -s "$source_script" "$target_script"; then
  mkdir -p "$UNIXNU_DIR/scripts"
  cp "$source_script" "$target_script"
  echo "copied build-s3910.py -> $target_script"
else
  echo "scripts/build-s3910.py already current (skip)"
fi

echo "DONE: S3910 wired into $UNIXNU_DIR"
