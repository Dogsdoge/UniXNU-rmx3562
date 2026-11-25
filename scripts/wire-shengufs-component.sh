#!/usr/bin/env bash
# wire-shengufs-component.sh —— 给 UniXNU 框架注册我们自定义的 MT6895 UFS 驱动组件
#
# 上游 components.toml 只有 ShengUfs（SM8550/高通），板级不兼容；我们板级
# board.toml 引用的是 ShengUfsMt6895，框架不认识 → defconfig 报 unknown component。
# 本脚本追加组件条目并把本地仓作为 drivers 源接入，重复运行幂等。
#
# 用法：bash wire-shengufs-component.sh <unixnu 框架目录> <ShengUfs-mt6895 仓目录>
set -euo pipefail

UNIXNU_DIR="${1:?usage: wire-shengufs-component.sh <unixnu dir> <shengufs dir>}"
SHENG_DIR="${2:?usage: wire-shengufs-component.sh <unixnu dir> <shengufs dir>}"
[ -f "$UNIXNU_DIR/components.toml" ] || { echo "no components.toml in $UNIXNU_DIR" >&2; exit 1; }
[ -f "$SHENG_DIR/build.py" ] || { echo "no build.py in $SHENG_DIR" >&2; exit 1; }

python3 - "$UNIXNU_DIR" "$SHENG_DIR" <<'PY'
import sys, pathlib
root, sheng = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
cat = root / 'components.toml'
text = cat.read_text()

if 'id = "ShengUfsMt6895"' not in text:
    entry = '''
[[component]]
id = "ShengUfsMt6895"
kind = "driver"
function = "MT6895 three-layer UFS block driver (HakiOS HC + extent map + IOMedia glue)"
scope = "board"
repository = "https://github.com/Dogsdoge/ShengUfs-mt6895.git"
revision = "c992abbfbf6cb0e3aae6dc5256e0f34b1b85e454"
path = "drivers/ShengUfsMt6895"
hardware = ["mediatek,mt6895 / mt8183-ufshci @ 0x112b0000"]
os_abi = ["ios-16.5-20F66 (xnu-8796.121.2)"]
board_dependencies = ["ShengUfsMt6895"]
verified_devices = []
dependencies = []
source_files = ["UfsHc.c", "UfsExtents.c", "Mt6895UfsBlock.cpp"]
runtime_identifiers = ["ShengUfsMt6895", "UfsHc.c", "UfsExtents.c", "Mt6895UfsBlock.cpp"]
build_stage = "kernel"
build = ["build.py"]
outputs = ["build/sheng-ufs/libShengUfs.a"]
'''
    with cat.open('a') as f:
        f.write(entry)
    print('added ShengUfsMt6895 component to components.toml')
else:
    print('ShengUfsMt6895 already registered (skip)')
PY

# 把本地仓作为 drivers/ShengUfsMt6895 接入（框架用 --local-drivers 或直接软链）
target="$UNIXNU_DIR/drivers/ShengUfsMt6895"
if [ ! -e "$target" ]; then
  mkdir -p "$UNIXNU_DIR/drivers"
  ln -s "$SHENG_DIR" "$target"
  echo "linked $target -> $SHENG_DIR"
else
  echo "$target already exists (skip)"
fi

# The framework assembles each component's build script into tree/scripts/
# and preflight checks for tree/scripts/build.py — copy ours into the
# framework scripts dir so "build = ['build.py']" resolves there.
if [ ! -f "$UNIXNU_DIR/scripts/build.py" ]; then
  cp "$SHENG_DIR/build.py" "$UNIXNU_DIR/scripts/build.py"
  echo "copied build.py -> $UNIXNU_DIR/scripts/build.py"
else
  echo "scripts/build.py already exists (skip)"
fi

echo "DONE: ShengUfsMt6895 wired into $UNIXNU_DIR"
