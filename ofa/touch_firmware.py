#!/usr/bin/env python3
"""Build the type-4 touchscreen extension offline from the exact stock asset.

No Bluetooth. The 80-byte helper is assembled from touch_patch.S/touch_patch.ld.
It is appended to the payload, inside the stock image's existing final flash
sector. No existing data, initialization table, or other profile is repurposed.
"""
import argparse
import json
import struct
from pathlib import Path

from firmware import (MP_SIZE, IMAGE_HEADER_SIZE, SHA_OFFSET, POOL_BASE, ROWS,
                      build_candidate, image_digest, sha256, verify_image)

HOOK_OFFSET = 0xD1CC
APPEND_OFFSET = 0x198CC
HOOK = bytes.fromhex('0cf07ebb')
CODE = bytes.fromhex(
    'a9f10d01092903d80c48445cf3f783bc00240b4800eb8600c07d000904280ad1'
    '08a0062104784c4504d002300139f9d1002400e04478f3f76ebc00bf'
    '363f8200a0a220002d0d30122e132f1e1b1f1f20')
KEYS = [
    {'name': 'Red', 'logical': 0x2D, 'pool': 13, 'usage': 0x69, 'linux': 'KEY_F14'},
    {'name': 'Green', 'logical': 0x30, 'pool': 18, 'usage': 0x6A, 'linux': 'KEY_F15'},
    {'name': 'Yellow', 'logical': 0x2E, 'pool': 19, 'usage': 0x6B, 'linux': 'KEY_F16'},
    {'name': 'Blue', 'logical': 0x2F, 'pool': 30, 'usage': 0x6C, 'linux': 'KEY_F17'},
    {'name': 'Return / Last CH', 'logical': 0x1B, 'pool': 31, 'usage': 0x6D, 'linux': 'KEY_F18'},
    {'name': 'Info', 'logical': 0x1F, 'pool': 32, 'usage': 0x6E, 'linux': 'KEY_F19'},
]


def build_touch_candidate(stock):
    physical, _ = build_candidate(stock)
    if len(physical) != APPEND_OFFSET:
        raise ValueError('Unexpected APP end')
    # A sector allocated/programmed for the stock image already contains this
    # tail. This is a bound on placement, not proof of bootloader recovery.
    def sector_end(size):
        return (0x80DE00 + size + 0xFFF) & ~0xFFF
    if sector_end(len(physical)) != sector_end(len(physical) + len(CODE)):
        raise ValueError('Extension would require an additional flash sector')
    used = {i for base in ROWS.values() for i in physical[base:base + 30]}
    used.update(physical[0x16136:0x16140])
    if used.intersection(k['pool'] for k in KEYS):
        raise ValueError('A touchscreen pool entry is already in use')
    out = bytearray(physical)
    out[HOOK_OFFSET:HOOK_OFFSET + len(HOOK)] = HOOK
    # Unreachable original instructions remain byte-for-byte intact.
    out.extend(CODE)
    for key in KEYS:
        struct.pack_into('<H', out, POOL_BASE + 2 * key['pool'], key['usage'])
    struct.pack_into('<I', out, MP_SIZE + 8, len(out) - MP_SIZE - IMAGE_HEADER_SIZE)
    out[SHA_OFFSET:SHA_OFFSET + 32] = image_digest(out)
    verify_image(out)
    allowed = set(range(HOOK_OFFSET, HOOK_OFFSET + 4))
    allowed.update(range(SHA_OFFSET, SHA_OFFSET + 32))
    allowed.update(range(MP_SIZE + 8, MP_SIZE + 12))
    for key in KEYS:
        allowed.update(range(POOL_BASE + 2 * key['pool'], POOL_BASE + 2 * key['pool'] + 2))
    changes = [{'offset': hex(i), 'before': f'{a:02x}', 'after': f'{b:02x}'}
               for i, (a, b) in enumerate(zip(physical, out)) if a != b]
    if any(int(c['offset'], 16) not in allowed for c in changes):
        raise ValueError('Change outside touchscreen allowlist')
    return bytes(out), {
        'status': 'OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED',
        'base': verify_image(physical), 'candidate': verify_image(out),
        'keys': KEYS, 'hid_page': 7,
        'scope': 'Six extra touchscreen keys, type 4 and BLE-enabled only',
        'changes_from_ble24': changes,
        'appended': {'offset': hex(APPEND_OFFSET), 'size': len(CODE), 'sha256': sha256(CODE)},
        'placement': 'Existing final 4 KiB APP sector; no existing payload bytes shifted',
        'limitations': ['Boot and live radio behavior require hardware verification',
                        'No guaranteed recovery after a bad boot'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    image, manifest = build_touch_candidate(args.stock.read_bytes())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'candidate-0.5.7-ble24-touch6.bin').write_bytes(image)
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
