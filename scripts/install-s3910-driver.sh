#!/usr/bin/env bash
# install-s3910-driver.sh —— 把 S3910 触控骨架注入已打 redwood.patch 的 XNU 源码树
#
# redwood 系列驱动靠三处改动进入内核：
#   1. bsd/conf/files          注册 redwood_s3910.c
#   2. bsd/sys/redwood_input.h 声明 redwood_s3910_init()
#   3. bsd/kern/bsd_init.c     在 redwood_input_init() 旁调用
# 本脚本按「锚点行」定位插入，不依赖行号；重复运行不会重复插入（幂等）。
#
# 用法：bash install-s3910-driver.sh <xnu/files 目录> <driver 目录>
# 例：  bash install-s3910-driver.sh /path/to/xnu/files ./driver
set -euo pipefail

XNU_FILES="${1:?usage: install-s3910-driver.sh <xnu/files dir> <driver dir>}"
DRIVER_DIR="${2:?usage: install-s3910-driver.sh <xnu/files dir> <driver dir>}"

[ -d "$XNU_FILES" ] || { echo "no such dir: $XNU_FILES" >&2; exit 1; }
[ -f "$DRIVER_DIR/redwood_s3910.c" ] || { echo "missing redwood_s3910.c in $DRIVER_DIR" >&2; exit 1; }

for f in bsd/conf/files bsd/sys/redwood_input.h bsd/kern/bsd_init.c bsd/dev; do
  [ -e "$XNU_FILES/$f" ] || { echo "missing $XNU_FILES/$f — is this a redwood-patched XNU tree?" >&2; exit 1; }
done

cp "$DRIVER_DIR/redwood_s3910.c" "$XNU_FILES/bsd/dev/redwood_s3910.c"
echo "copied driver -> $XNU_FILES/bsd/dev/redwood_s3910.c"

python3 - "$XNU_FILES" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])

edits = [
    # (relative path, anchor line, line to insert after anchor, description)
    ('bsd/conf/files', 'bsd/dev/redwood_input.c', 'bsd/dev/redwood_s3910.c\t\tstandard', 'driver registration'),
    ('bsd/sys/redwood_input.h', 'void redwood_tcm_init(void);', 'void redwood_s3910_init(void);', 'init declaration'),
    ('bsd/kern/bsd_init.c', 'redwood_input_init();', '\t\tredwood_s3910_init();', 'init call'),
]

for rel, anchor, addition, label in edits:
    path = root / rel
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    if addition.strip() in text:
        print('skip %-22s (already present)' % label)
        continue
    hits = [i for i, line in enumerate(lines) if anchor in line]
    if len(hits) != 1:
        raise SystemExit('%s: expected exactly one anchor %r in %s, found %d' % (label, anchor, rel, len(hits)))
    at = hits[0] + 1
    eol = '\r\n' if lines[hits[0]].endswith('\r\n') else '\n'
    lines.insert(at, addition + eol)
    path.write_text(''.join(lines))
    print('patched %-22s -> %s:%d' % (label, rel, at + 1))
PY

echo "DONE: S3910 driver wired into $XNU_FILES"
