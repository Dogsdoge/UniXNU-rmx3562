#!/usr/bin/env python3
"""Generate a UniXNU SHNGROOT manifest block for the iOS root image.

The manifest is written into the FIRST 4096-byte block of the pinned file in
userdata. ShengUfs's findRootLayout() (RootLayout.h:148-212) parses it.

Layout (little-endian, 4096-byte block):
  +0     magic "SHNGROOT" (8 bytes)
  +8     version  u32   (1 = windows only)
  +12     n        u32   (number of window entries, 1..4)
  +16     imageBytes u64 (total size of flashed root image, 4K aligned)
  +24     entries: n * 128 bytes each
            +0   name[32]  (NUL-terminated printable ASCII)
            +32  offset    u64  (bytes from start of root image, 4K aligned, >= 4096)
            +40  size      u64  (bytes)
            +48  blockSize u32  (512 for window)
            +52  flags     u32  (1 for window)
            +56  sha256[32]
  +4064  SHA256 of bytes [0, 4064)

Note: this is the MANIFEST only. The actual root image follows the manifest
inside the pinned file. With the extent layer, ShengUfs reads the manifest via
the extent map instead of the raw userdata first block.
"""
import argparse, hashlib, struct, sys

MAGIC = b'SHNGROOT'
BLOCK = 4096
ENTRY_SIZE = 128
ENTRY_BASE = 24
DIGEST_OFF = 4064

def build(version, windows, image_bytes):
    if not (1 <= version <= 2):
        raise ValueError('version must be 1 or 2')
    if not (1 <= len(windows) <= 4):
        raise ValueError('windows count must be 1..4')
    if image_bytes < BLOCK or image_bytes % BLOCK:
        raise ValueError('image_bytes must be >= 4096 and 4K aligned')
    buf = bytearray(BLOCK)
    buf[0:8] = MAGIC
    struct.pack_into('<I', buf, 8, version)
    struct.pack_into('<I', buf, 12, len(windows))
    struct.pack_into('<Q', buf, 16, image_bytes)
    used = []
    for i, w in enumerate(windows):
        e = ENTRY_BASE + i * ENTRY_SIZE
        name = w['name'].encode('ascii')
        if len(name) >= 32 or not name:
            raise ValueError('window name must be 1..31 printable ASCII')
        buf[e:e+len(name)] = name
        off = w['offset']; size = w['size']
        if off < BLOCK or off % BLOCK or off >= image_bytes:
            raise ValueError(f"{w['name']}: bad offset {off}")
        if size == 0 or size > image_bytes - off:
            raise ValueError(f"{w['name']}: bad size {size}")
        struct.pack_into('<Q', buf, e+32, off)
        struct.pack_into('<Q', buf, e+40, size)
        struct.pack_into('<I', buf, e+48, w.get('block', 512))
        struct.pack_into('<I', buf, e+52, w.get('flags', 1))
        sha = bytes.fromhex(w['sha256']) if 'sha256' in w else hashlib.sha256(b'\0' * size).digest()
        buf[e+56:e+88] = sha
        # overlap check (with 4K padding)
        end = (off + size + BLOCK - 1) & ~(BLOCK - 1)
        for p0, p1 in used:
            if off < p1 and p0 < end:
                raise ValueError(f"{w['name']}: overlaps a prior window")
        used.append((off, end))
    digest = hashlib.sha256(bytes(buf[:DIGEST_OFF])).digest()
    buf[DIGEST_OFF:DIGEST_OFF+32] = digest
    return bytes(buf)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True, help='output manifest block file')
    ap.add_argument('--version', type=int, default=1)
    ap.add_argument('--image-bytes', type=int, required=True, help='total root image size (4K aligned)')
    ap.add_argument('--window', action='append', required=True,
                    help='name=offset=size[=sha256hex] (4K aligned bytes)')
    args = ap.parse_args()
    windows = []
    for spec in args.window:
        parts = spec.split('=')
        if len(parts) < 3:
            raise SystemExit(f'bad --window: {spec}')
        w = {'name': parts[0], 'offset': int(parts[1], 0), 'size': int(parts[2], 0)}
        if len(parts) >= 4:
            w['sha256'] = parts[3]
        windows.append(w)
    block = build(args.version, windows, args.image_bytes)
    with open(args.out, 'wb') as f:
        f.write(block)
    print(f'wrote {args.out}: {len(block)} bytes, {len(windows)} windows, image {args.image_bytes} bytes')

if __name__ == '__main__':
    main()
