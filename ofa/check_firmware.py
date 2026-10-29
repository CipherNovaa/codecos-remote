#!/usr/bin/env python3
"""Validate the image format and execute the actual ARM matrix lookup offline.

Usage: python check_firmware.py ~/.cache/ofa/apk/x/assets
Requires unicorn (use /tmp/ofa-audit-venv/bin/python in this session).
"""
import argparse
import struct
from pathlib import Path

from firmware import (BUTTONS, NEW_KEYS, POOL_BASE, ROWS, build_candidate,
                      button_map, verify_image)


def execute_lookup(data, profile, slot, matrix_index):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_SP, UC_ARM_REG_LR)

    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    cpu.mem_map(0x800000, 0x40000)
    cpu.mem_write(0x80DE00, data)
    cpu.mem_map(0x200000, 0x10000)
    record_base = struct.unpack_from("<I", data, 0xF9FC)[0]
    cpu.mem_write(record_base + 4 * slot + 0x17, bytes([profile << 4]))
    cpu.reg_write(UC_ARM_REG_SP, 0x20F000)
    cpu.reg_write(UC_ARM_REG_LR, 0x830001)
    cpu.reg_write(UC_ARM_REG_R0, slot)
    cpu.reg_write(UC_ARM_REG_R1, matrix_index // 6)
    cpu.reg_write(UC_ARM_REG_R2, matrix_index % 6)
    cpu.emu_start(0x81D42F, 0x830000, count=100)
    return cpu.reg_read(UC_ARM_REG_R0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", type=Path)
    args = parser.parse_args()
    images = sorted(args.assets.glob("ofa_touch_*.bin"))
    assert images, "No firmware assets found"
    for image in images:
        verify_image(image.read_bytes())
    print(f"PASS: Realtek SHA-256 reproduced for {len(images)} original firmware assets")
    stock = (args.assets / "ofa_touch_0.5.7.bin").read_bytes()
    candidate, manifest = build_candidate(stock)
    checks = 0
    for data in (stock, candidate):
        for profile, base in ROWS.items():
            for slot in (1, 4, 12):
                for matrix_index in range(30):
                    actual = execute_lookup(data, profile, slot, matrix_index)
                    assert actual == data[base + matrix_index], (profile, slot, matrix_index, actual)
                    checks += 1
    print(f"PASS: {checks} actual ARM lookup executions (all cells, all profiles, three device slots)")
    assert len(button_map(candidate)) == len(BUTTONS) == 24
    assert len({key["scan"] for key in button_map(candidate)}) == 24
    assert len(NEW_KEYS) == 10
    # Altered payload/header coverage must fail integrity validation. Changing
    # even a valid image to an unrecognized version must fail patch pinning.
    for offset in (0x210, 0x3A0, 0x5F0, 0x1000, len(stock) - 1):
        damaged = bytearray(stock)
        damaged[offset] ^= 1
        try:
            verify_image(damaged)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Corruption accepted at {offset:#x}")
    for invalid in (stock[:-1], candidate, images[0].read_bytes()):
        try:
            build_candidate(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Wrong/truncated/already-patched input accepted")
    assert build_candidate(stock)[0] == candidate
    print("PASS: corruption, length, version pinning, duplicate-report and other-profile checks")
    print(f"Candidate: {manifest['candidate']['sha256']}")
    print("Hardware behavior remains untested; these checks do not establish boot or OTA safety.")


if __name__ == "__main__":
    main()
