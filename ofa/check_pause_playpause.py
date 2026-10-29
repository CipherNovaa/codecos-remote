#!/usr/bin/env python3
"""Offline checks for the cumulative type-4 Pause -> Play/Pause correction."""
import argparse
import struct
from pathlib import Path

from firmware import POOL_BASE, ROWS, verify_image
from pause_playpause_firmware import build
from type4_parity_firmware import build as build_type4_parity


def usage(image, pool):
    return struct.unpack_from('<H', image, POOL_BASE + 2 * pool)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    args = parser.parse_args()
    stock = args.stock.read_bytes()
    base, _ = build_type4_parity(stock)
    candidate, manifest = build(stock)
    assert base[ROWS[4] + 1] == 46 and base[ROWS[4] + 25] == 45
    assert usage(base, 46) == 0x00B1 and usage(base, 45) == 0x00B0
    assert usage(candidate, 46) == 0x00CD and usage(candidate, 45) == 0x00B0
    for pool in range(64):
        if pool != 46:
            assert usage(candidate, pool) == usage(base, pool)
    assert candidate[ROWS[4]:ROWS[4] + 30] == base[ROWS[4]:ROWS[4] + 30]
    verify_image(candidate)
    # The high byte remains zero for both 0x00b1 and 0x00cd, so it need not
    # appear in the byte-difference list. The builder independently enforces
    # the exact two-byte pool allowlist and full digest range.
    assert 2 <= len(manifest['changes']) <= 34
    print('PASS: physical Pause pool 46 -> HID Consumer Play/Pause 0x00cd')
    print('PASS: physical Play pool 45 remains HID Consumer Play 0x00b0')
    print('PASS: matrix, other profiles, and prior Channel correction preserved')


if __name__ == '__main__':
    main()
