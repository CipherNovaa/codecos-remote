#!/usr/bin/env python3
"""Build the cumulative type-4 pause-to-Play/Pause HID correction."""
import argparse
import json
import struct
from pathlib import Path

from firmware import POOL_BASE, SHA_OFFSET, image_digest, sha256, verify_image
from type4_parity_firmware import build as build_type4_parity

BASE_SHA256 = '1f0aa8fb22ada7cf11d822d6eed1aa87f39850d5261e14240984b4262669f6b4'
PAUSE_POOL = 46


def build(stock):
    base, _ = build_type4_parity(stock)
    if sha256(base) != BASE_SHA256:
        raise ValueError('Expected the pinned type-4 parity base image')
    out = bytearray(base)
    offset = POOL_BASE + 2 * PAUSE_POOL
    if struct.unpack_from('<H', out, offset)[0] != 0x00B1:
        raise ValueError('Unexpected Pause HID pool usage')
    struct.pack_into('<H', out, offset, 0x00CD)
    out[SHA_OFFSET:SHA_OFFSET + 32] = image_digest(out)
    verified = verify_image(out)
    changes = [(i, base[i], out[i]) for i in range(len(out)) if base[i] != out[i]]
    allowed = {offset, offset + 1, *range(SHA_OFFSET, SHA_OFFSET + 32)}
    if {i for i, _, _ in changes} - allowed:
        raise ValueError('Change outside Pause HID allowlist')
    return bytes(out), {
        'status': 'OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED',
        'base_sha256': BASE_SHA256,
        'candidate': verified,
        'scope': 'Type-4 DESKTOP and LAPTOP: retain prior matrix corrections; Pause HID 0x00b1 -> 0x00cd',
        'changes': [{'offset': hex(i), 'before': f'{before:02x}', 'after': f'{after:02x}'}
                    for i, before, after in changes],
        'preserved': ['physical Play HID 0x00b0', 'all matrix assignments', 'types 1-3',
                      'screen firmware', 'profile records', 'Bluetooth bonds', 'custom labels'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    image, manifest = build(args.stock.read_bytes())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'candidate-0.5.7-pause-playpause.bin').write_bytes(image)
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
