#!/usr/bin/env python3
"""Offline checks for the cumulative type-4 Channel and Play/Pause correction."""
import argparse
from pathlib import Path

from channel_swap_firmware import build as build_channel_swap
from firmware import POOL_BASE, ROWS, button_map, verify_image
from type4_parity_firmware import build


def usage(image, cell):
    pool = image[ROWS[4] + cell]
    return pool, int.from_bytes(image[POOL_BASE + 2 * pool:POOL_BASE + 2 * pool + 2], 'little')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    args = parser.parse_args()
    stock = args.stock.read_bytes()
    base, _ = build_channel_swap(stock)
    candidate, manifest = build(stock)
    assert usage(base, 1) == (45, 0xB0)
    assert usage(base, 25) == (46, 0xB1)
    assert usage(candidate, 1) == (46, 0xB1)
    assert usage(candidate, 25) == (45, 0xB0)
    assert usage(candidate, 11) == (43, 0x9C)
    assert usage(candidate, 16) == (44, 0x9D)
    for cell in range(30):
        if cell not in (1, 25):
            assert candidate[ROWS[4] + cell] == base[ROWS[4] + cell]
    assert candidate[POOL_BASE:POOL_BASE + 128] == base[POOL_BASE:POOL_BASE + 128]
    for row in (1, 2, 3):
        assert candidate[ROWS[row]:ROWS[row] + 30] == base[ROWS[row]:ROWS[row] + 30]
    verify_image(candidate)
    mapped = {entry['button']: entry['usage'] for entry in button_map(candidate)}
    assert mapped['Play'] == '0xb1' and mapped['Pause'] == '0xb0'
    assert len(manifest['changes']) == 34
    print('PASS: prior Channel correction retained; Play/Pause physical assignments swapped')
    print('PASS: only two type-4 matrix bytes plus the image digest changed from installed base')


if __name__ == '__main__':
    main()
