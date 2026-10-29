#!/usr/bin/env python3
"""Build the audited type-4 Channel Up/Down correction from custom-touch v2.

Only the two type-4 matrix entries are swapped.  The HID pool, code, screen
firmware, profile records and Bluetooth bond storage are not changed.
"""
import argparse
import json
from pathlib import Path

from custom_firmware import build_custom_candidate
from firmware import ROWS, SHA_OFFSET, image_digest, sha256, verify_image

BASE_SHA256 = '1f5db4d75778e930f5c4b9653a2121a1991d8fada0d13219a584f78504364e64'
CHANNEL_DOWN_CELL = 11
CHANNEL_UP_CELL = 16


def build(stock):
    base, _ = build_custom_candidate(stock)
    if sha256(base) != BASE_SHA256:
        raise ValueError('Expected the pinned custom-touch v2 base image')
    out = bytearray(base)
    down = ROWS[4] + CHANNEL_DOWN_CELL
    up = ROWS[4] + CHANNEL_UP_CELL
    if (out[down], out[up]) != (44, 43):
        raise ValueError('Unexpected type-4 Channel Up/Down mapping')
    out[down], out[up] = out[up], out[down]
    out[SHA_OFFSET:SHA_OFFSET + 32] = image_digest(out)
    verified = verify_image(out)
    changes = [(offset, base[offset], out[offset]) for offset in range(len(out)) if base[offset] != out[offset]]
    allowed = {down, up, *range(SHA_OFFSET, SHA_OFFSET + 32)}
    if {offset for offset, _, _ in changes} - allowed:
        raise ValueError('Change outside Channel Up/Down allowlist')
    return bytes(out), {
        'status': 'OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED',
        'base_sha256': BASE_SHA256,
        'candidate': verified,
        'scope': 'Type-4 DESKTOP and LAPTOP only: swap Channel Up/Down HID matrix cells',
        'changes': [{'offset': hex(offset), 'before': f'{before:02x}', 'after': f'{after:02x}'}
                    for offset, before, after in changes],
        'preserved': ['HID usage pool', 'all other type-4 buttons', 'types 1-3',
                      'screen firmware', 'profile records', 'Bluetooth bonds', 'custom labels'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    image, manifest = build(args.stock.read_bytes())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'candidate-0.5.7-channel-swap.bin').write_bytes(image)
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
