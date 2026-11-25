#!/usr/bin/env python3
"""Generate PlatformMemoryMapLib.c for RMX3562 from the live reserved-memory map.

Input: docs/rmx3562-memory-map.json (46 mblock regions, read-only captured
from /proc/device-tree/reserved-memory on 2026-11-24).
Output: C array for CrackerCat's EDK2 MT6895Pkg PlatformMemoryMapLib.

The array is sorted by address. Reserved mblock regions are emitted as MEM_RES,
and the gaps between them as ordinary SYS_MEM ("DDR <addr>") so EDK2's GCD
covers all memory while keeping reserved regions out of allocation.
"""
import json
from pathlib import Path

JSON = Path('docs/rmx3562-memory-map.json')
OUT = Path('PlatformMemoryMapLib.rmx3562.c')

# Low-memory fixed regions (same layout as pearl, before the first mblock at 0x48080000).
LOW = [
    ('Periphs',      0x0000000000, 0x040000000, 'MEM_RES',    'UNCACHEABLE',  'RtCode', 'NS_DEVICE'),
    ('UEFI DDR Low', 0x0040000000, 0x00c00000,  'SYS_MEM',    'SYS_MEM_CAP',  'Conv',   'WRITE_BACK'),
    ('UEFI Stack',   0x0040c00000, 0x00040000,  'SYS_MEM',    'SYS_MEM_CAP',  'BsData', 'WRITE_BACK'),
    ('CPU Vectors',  0x0040c40000, 0x00010000,  'SYS_MEM',    'SYS_MEM_CAP',  'BsCode', 'WRITE_BACK'),
    ('DXE Heap',     0x0040c50000, 0x007430000, 'SYS_MEM',    'SYS_MEM_CAP',  'Conv',   'WRITE_BACK'),
]

# Memory ends at 12 GiB = 0x3_0000_0000 (last region 0x33fffe000+0x2000 is near 13GB,
# but the phone reports 12 GiB; use 0x3_0000_0000 as the top for the GCD).
MEM_TOP = 0x300000000

def region(label, base, size, restype, attr, memtype, cache):
    return (f'    {{"{label:<28}", 0x{base:011x}, 0x{size:010x}, AddMem, '
            f'{restype:<9}, {attr:<14}, {memtype:<6}, {cache}}},')

def main():
    data = json.loads(JSON.read_text())
    regs = [(int(r['addr'], 16), int(r['size'], 16), r['name']) for r in data['regions']]
    regs.sort()
    lines = []
    lines.append('/*')
    lines.append(' * RMX3562 (realme GT Neo3, MT6895 / Dimensity 8100) 12GB DRAM memory map.')
    lines.append(' * Generated from live /proc/device-tree/reserved-memory (2026-11-24).')
    lines.append(' * Reserved mblocks are MEM_RES; gaps are filled with ordinary DDR regions.')
    lines.append(' * Framebuffer: 0xfe1af000, 1080x2412, LK scanout, WRITE_THROUGH_XN.')
    lines.append(' */')
    lines.append('#include <Library/BaseLib.h>')
    lines.append('#include <Library/PlatformMemoryMapLib.h>')
    lines.append('')
    lines.append('static ARM_MEMORY_REGION_DESCRIPTOR_EX gDeviceMemoryDescriptorEx[] = {')
    for r in LOW:
        lines.append(region(*r))

    cursor = LOW[-1][1] + LOW[-1][2]   # 0x48080000
    for base, size, name in regs:
        if base < cursor:
            continue  # overlap with low fixed region; skip
        if base > cursor:
            lines.append(region(f'DDR 0x{cursor:x}', cursor, base - cursor,
                                'SYS_MEM', 'SYS_MEM_CAP', 'Conv', 'WRITE_BACK'))
        # Region names are stored in a 32-byte char array (31 + NUL).
        short = name.replace('mblock-', 'Res ')[:31]
        cache = 'WRITE_THROUGH_XN' if 'framebuffer' in name else 'WRITE_BACK'
        lines.append(region(short, base, size, 'MEM_RES', 'SYS_MEM_CAP', 'Reserv', cache))
        cursor = base + size

    if cursor < MEM_TOP:
        lines.append(region(f'DDR 0x{cursor:x}', cursor, MEM_TOP - cursor,
                            'SYS_MEM', 'SYS_MEM_CAP', 'Conv', 'WRITE_BACK'))

    lines.append('')
    lines.append('    //------------------- Terminator for MMU ---------------------')
    lines.append('    {"Terminator", 0, 0, 0, 0, 0, 0, 0}};')
    lines.append('')
    lines.append('ARM_MEMORY_REGION_DESCRIPTOR_EX *GetPlatformMemoryMap()')
    lines.append('{')
    lines.append('  return gDeviceMemoryDescriptorEx;')
    lines.append('}')
    OUT.write_text('\n'.join(lines) + '\n')
    print(f'generated {OUT}: {len(lines)} lines, {len(regs)} reserved regions')

if __name__ == '__main__':
    main()
