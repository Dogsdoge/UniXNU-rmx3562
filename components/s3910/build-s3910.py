#!/usr/bin/env python3
"""Install the S3910 touch driver into the XNU work tree.

The unixnu driver runs component recipes from the assembled tree during
`build_archives`, after `patches.prepare()` has created and patched
`<tree>/.cache/xnu-work`. That is the only point where the XNU root exists and
is already patched, so this script is the injection step:

  * copies redwood_s3910.c and the TouchComm protocol layer (SynaTcm.c/.h) from
    the device repository into bsd/dev/;
  * when the tree has no redwood port, installs the minimal /dev/redwood-touch
    layer from this directory (bsd/sys/redwood_input.h, bsd/dev/redwood_input.c);
  * registers the new sources in bsd/conf/files;
  * calls redwood_input_init() and redwood_s3910_init() from bsd_init.c after
    devfs_kernel_mount(mounthere), the same point the redwood port uses.

Every edit checks for its own result first, so a rerun and the bootstrap pass
are safe. No hardware value is invented here; the driver keeps its
TODO(BRINGUP) markers.

This replaces install-s3910-driver.sh. Its "<xnu/files dir>" argument was
wrong: the target is the applied XNU tree root (<tree>/.cache/xnu-work), not
a files/ directory. This recipe takes no argument -- cwd is that tree.

Environment: UNIXNU_DEVICE (set by unixnu), cwd = assembled tree.
"""

import os
import shutil
import sys
from pathlib import Path

TREE = Path.cwd()
XNU = TREE / '.cache' / 'xnu-work'
DEVICE = Path(os.environ.get('UNIXNU_DEVICE') or '.').resolve()
COMPONENT = DEVICE / 'components' / 's3910'

CONF_FILES = XNU / 'bsd' / 'conf' / 'files'
BSD_INIT = XNU / 'bsd' / 'kern' / 'bsd_init.c'
REDWOOD_HEADER = XNU / 'bsd' / 'sys' / 'redwood_input.h'

# bsd/conf/files lists each bsd/dev source on a line of its own. The redwood
# patch inserts at the same place, so the anchor is a file every 8796 tree has.
CONF_ANCHORS = ('bsd/dev/unix_startup.c', 'bsd/dev/monotonic.c')
INIT_ANCHOR = 'devfs_kernel_mount(mounthere);'
INCLUDE_ANCHOR = '#include <miscfs/devfs/devfsdefs.h>'


def fail(message):
    print(f'build-s3910: {message}', file=sys.stderr)
    sys.exit(1)


def read(path):
    try:
        return path.read_text()
    except OSError as error:
        fail(f'{path}: {error}')


def find_unique(lines, needle, what):
    hits = [index for index, line in enumerate(lines) if needle in line]
    if len(hits) != 1:
        fail(f'{what}: expected one line containing {needle!r}, found {len(hits)}')
    return hits[0]


def ensure_after(lines, needle, added, what):
    """Insert `added` after the single line containing `needle`, once."""
    if any(line.strip() == added.strip() for line in lines):
        return False
    index = find_unique(lines, needle, what)
    lines.insert(index + 1, added)
    return True


def main():
    if not CONF_FILES.is_file():
        fail(f'no XNU work tree at {XNU} (bsd/conf/files missing)')

    sources = {
        'redwood_s3910.c': DEVICE / 'driver' / 'redwood_s3910.c',
        'SynaTcm.c': DEVICE / 'reference' / 's3910' / 'SynaTcm.c',
        'SynaTcm.h': DEVICE / 'reference' / 's3910' / 'SynaTcm.h',
    }
    for name, path in sources.items():
        if not path.is_file() or path.stat().st_size == 0:
            fail(f'missing source {path}')

    # A full redwood port ships redwood_tcm_init (and the other driver inits) in
    # its header; our minimal header does not. Never duplicate its input layer.
    full_port = REDWOOD_HEADER.is_file() and 'redwood_tcm_init' in read(REDWOOD_HEADER)

    dev = XNU / 'bsd' / 'dev'
    dev.mkdir(parents=True, exist_ok=True)
    installed = []
    for name in ('redwood_s3910.c', 'SynaTcm.c', 'SynaTcm.h'):
        shutil.copy2(sources[name], dev / name)
        installed.append(f'bsd/dev/{name}')

    if full_port:
        header = read(REDWOOD_HEADER).splitlines(keepends=True)
        if 'redwood_s3910_init' not in ''.join(header):
            ensure_after(header, 'void redwood_touch_post(', 'void redwood_s3910_init(void);\n',
                         'redwood header')
            REDWOOD_HEADER.write_text(''.join(header))
    else:
        for name, target in (('redwood_input.h', XNU / 'bsd' / 'sys' / 'redwood_input.h'),
                             ('redwood_input.c', dev / 'redwood_input.c')):
            source = COMPONENT / name
            if not source.is_file():
                fail(f'missing source {source}')
            shutil.copy2(source, target)
            installed.append(f'bsd/dev/{name}' if name.endswith('.c') else f'bsd/sys/{name}')

    # bsd/conf/files: one line per source. Skip what is already registered.
    conf = read(CONF_FILES).splitlines(keepends=True)
    wanted = ['bsd/dev/redwood_s3910.c', 'bsd/dev/SynaTcm.c']
    if not full_port:
        wanted.insert(0, 'bsd/dev/redwood_input.c')
    missing = [entry for entry in wanted
               if not any(line.split() and line.split()[0] == entry for line in conf if line.strip())]
    if missing:
        for anchor in CONF_ANCHORS:
            hits = [index for index, line in enumerate(conf) if anchor in line]
            if len(hits) == 1:
                break
        else:
            fail(f'{CONF_FILES}: no unique anchor among {CONF_ANCHORS}')
        block = ''.join(f'{entry}\t\tstandard\n' for entry in missing)
        conf.insert(hits[0] + 1, block)
        CONF_FILES.write_text(''.join(conf))

    # bsd/kern/bsd_init.c: the header and the two init calls.
    init = read(BSD_INIT).splitlines(keepends=True)
    if '#include <sys/redwood_input.h>' not in ''.join(init):
        ensure_after(init, INCLUDE_ANCHOR, '#include <sys/redwood_input.h>\n', 'bsd_init.c include')
    if 'redwood_input_init();' not in ''.join(init):
        ensure_after(init, INIT_ANCHOR, '\t\tredwood_input_init();\n', 'bsd_init.c mount')
    if 'redwood_s3910_init();' not in ''.join(init):
        if 'redwood_input_init();' in ''.join(init):
            ensure_after(init, 'redwood_input_init();', '\t\tredwood_s3910_init();\n', 'bsd_init.c redwood')
        else:
            ensure_after(init, INIT_ANCHOR, '\t\tredwood_s3910_init();\n', 'bsd_init.c mount')
    BSD_INIT.write_text(''.join(init))

    print('build-s3910: ' + ('redwood port found; ' if full_port else 'minimal redwood input; ') +
          'installed ' + ', '.join(installed))
    return 0


if __name__ == '__main__':
    sys.exit(main())
