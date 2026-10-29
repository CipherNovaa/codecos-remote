#!/usr/bin/env python3
"""Offline integrity checks for the type-4 Channel Up/Down correction."""
import argparse
from pathlib import Path

from channel_swap_firmware import build
from custom_firmware import build_custom_candidate
from firmware import POOL_BASE, ROWS, button_map, verify_image


def usage(image, cell):
    pool = image[ROWS[4] + cell]
    return pool, int.from_bytes(image[POOL_BASE + 2 * pool:POOL_BASE + 2 * pool + 2], 'little')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    args = parser.parse_args()
    stock = args.stock.read_bytes()
    base, _ = build_custom_candidate(stock)
    candidate, manifest = build(stock)
    assert usage(base, 11) == (44, 0x9D)
    assert usage(base, 16) == (43, 0x9C)
    assert usage(candidate, 11) == (43, 0x9C)
    assert usage(candidate, 16) == (44, 0x9D)
    assert candidate[ROWS[4]:ROWS[4] + 30] != base[ROWS[4]:ROWS[4] + 30]
    for cell in range(30):
        if cell not in (11, 16):
            assert candidate[ROWS[4] + cell] == base[ROWS[4] + cell]
    assert candidate[POOL_BASE:POOL_BASE + 128] == base[POOL_BASE:POOL_BASE + 128]
    for row in (1, 2, 3):
        assert candidate[ROWS[row]:ROWS[row] + 30] == base[ROWS[row]:ROWS[row] + 30]
    verify_image(candidate)
    mapped = {entry['button']: entry['usage'] for entry in button_map(candidate)}
    assert mapped['ChannelDown'] == '0x9c'
    assert mapped['ChannelUp'] == '0x9d'
    assert len(manifest['changes']) == 34
    print('PASS: Channel Down -> HID 0x009c; Channel Up -> HID 0x009d')
    print('PASS: only two type-4 matrix bytes plus the image digest changed')


if __name__ == '__main__':
    main()
