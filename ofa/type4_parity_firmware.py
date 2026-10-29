#!/usr/bin/env python3
"""Build the cumulative type-4 physical-button parity correction.

Applies the verified Channel Up/Down correction, then swaps only the Play and
Pause physical matrix entries.  DESKTOP and LAPTOP share this type-4 matrix.
"""
import argparse
import json
from pathlib import Path

from channel_swap_firmware import build as build_channel_swap
from firmware import ROWS, SHA_OFFSET, image_digest, sha256, verify_image

BASE_SHA256 = 'e5e0c84f594c99458580984ccd1c8c70e39770697a4293fcca8aa59f61ac5f8c'
PLAY_CELL = 1
PAUSE_CELL = 25


def build(stock):
    base, _ = build_channel_swap(stock)
    if sha256(base) != BASE_SHA256:
        raise ValueError('Expected the pinned Channel Up/Down correction base image')
    out = bytearray(base)
    play = ROWS[4] + PLAY_CELL
    pause = ROWS[4] + PAUSE_CELL
    if (out[play], out[pause]) != (45, 46):
        raise ValueError('Unexpected type-4 Play/Pause mapping')
    out[play], out[pause] = out[pause], out[play]
    out[SHA_OFFSET:SHA_OFFSET + 32] = image_digest(out)
    verified = verify_image(out)
    changes = [(offset, base[offset], out[offset]) for offset in range(len(out)) if base[offset] != out[offset]]
    allowed = {play, pause, *range(SHA_OFFSET, SHA_OFFSET + 32)}
    if {offset for offset, _, _ in changes} - allowed:
        raise ValueError('Change outside Play/Pause allowlist')
    return bytes(out), {
        'status': 'OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED',
        'base_sha256': BASE_SHA256,
        'candidate': verified,
        'scope': 'Type-4 DESKTOP and LAPTOP: retain Channel correction; swap Play/Pause HID matrix cells',
        'changes': [{'offset': hex(offset), 'before': f'{before:02x}', 'after': f'{after:02x}'}
                    for offset, before, after in changes],
        'preserved': ['all other type-4 buttons', 'HID usage pool', 'types 1-3',
                      'screen firmware', 'profile records', 'Bluetooth bonds', 'custom labels'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    image, manifest = build(args.stock.read_bytes())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'candidate-0.5.7-type4-parity.bin').write_bytes(image)
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
