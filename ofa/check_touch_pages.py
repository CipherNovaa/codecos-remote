#!/usr/bin/env python3
"""Check page commands against the actual ARM config parser and live fixtures.

No Bluetooth. Emulates config reads/writes with persistence and notification
hooks, proving what the firmware stores and returns for the tool's commands.
"""
import argparse
import json
import struct
from pathlib import Path

from firmware import STOCK_SHA256, sha256
from touch_pages import decode_inventory, decode_pages, encode_pages, merge_pages
from device_order import deduplicate, order_from_inventory


class FirmwareConfig:
    def __init__(self, image):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                       UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)
        self.regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                     UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)
        self.cpu = cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        cpu.mem_map(0, 0x80000)
        cpu.mem_map(0x800000, 0x40000)
        cpu.mem_write(0x80DE00, image)
        cpu.mem_map(0x200000, 0x10000)
        cpu.mem_write(0x20A301, b'\x0c')
        cpu.hook_add(UC_HOOK_CODE, self.hook)
        self.writes = []
        self.reply = None

    def hook(self, cpu, address, size, _):
        r0, r1, r2, sp, lr, pc = self.regs
        a, b, c = (cpu.reg_read(r) for r in (r0, r1, r2))
        if address == 0x818382:
            length = struct.unpack('<H', cpu.mem_read(a - 0x222, 2))[0]
            self.reply = bytes(cpu.mem_read(a, length))
            cpu.emu_stop()
            return
        if address == 0xB918:
            self.writes.append((b, bytes(cpu.mem_read(a, c))))
        elif address in (0x5A01C, 0x5A060):
            cpu.mem_write(a, bytes(b))
        elif address == 0x59E9A:
            cpu.mem_write(a, bytes(cpu.mem_read(b, c)))
        elif address in (0x7A5E, 0x7F0A):
            pass  # Logging/formatting only.
        elif address < 0x800000:
            raise AssertionError(f'Unexpected ROM/hardware call {address:#x}')
        else:
            return
        cpu.reg_write(r0, 0)
        cpu.reg_write(pc, cpu.reg_read(lr))

    def command(self, payload):
        r0, r1, r2, sp, lr, pc = self.regs
        self.reply, self.writes = None, []
        self.cpu.mem_write(0x20E000, payload)
        self.cpu.reg_write(r0, len(payload))
        self.cpu.reg_write(r1, 0x20E000)
        self.cpu.reg_write(sp, 0x20F000)
        self.cpu.reg_write(lr, 0x830001)
        self.cpu.emu_start(0x818759, 0x830000, count=20000)
        assert self.reply is not None, 'Config handler did not reply'
        return self.reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    args = parser.parse_args()
    image = args.stock.read_bytes()
    assert sha256(image) == STOCK_SHA256
    # Saved live BRAVIA page reply; no proprietary firmware bundled here.
    bravia = decode_pages(bytes.fromhex(
        '07 02 01 fd 04 22 23 24 36 02 fe 10 '
        '0e 0f 10 11 12 13 14 15 16 0d 2d 30 2e 2f 1b 1f'))
    fixtures = [bravia, [bravia[1]], [],
                [{'id': i, 'layout': 0xFE, 'keys': list(range(1, 17))}
                 for i in range(1, 11)]]
    checks = 0
    for slot in range(1, 13):
        config = FirmwareConfig(image)
        for pages in fixtures:
            assert config.command(encode_pages(slot, pages)) == b'\x17\x01'
            assert len(config.writes) == 1
            offset, content = config.writes[0]
            assert offset == 0x1048 + slot * 192 and len(content) == 192
            reply = config.command(bytes([0x57, 7, slot]))
            assert decode_pages(reply) == pages, (slot, pages, reply.hex())
            assert not config.writes, 'A page read must not write persistence'
            # Check every OTHER slot via the real firmware reader too.
            for other in set(range(1, 13)) - {slot}:
                assert decode_pages(config.command(bytes([0x57, 7, other]))) == []
            checks += 1
    assert merge_pages(bravia, bravia) == bravia
    for damaged in (b'', b'\x07', b'\x07\x02', b'\x07\x00\x00',
                    b'\x07\x01\x01\xfe\x11' + bytes(17)):
        try:
            decode_pages(damaged)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed reply accepted')
    # Inventory must preserve holes and repeated identities from the live read.
    code = bytes.fromhex('41 0b 27 80 00 05') + b'CODEC'.ljust(16, b'\0')
    sony = bytes.fromhex('13 d3 00 80 0d 57') + b'BRAVIA'.ljust(16, b'\0')
    fixture = b'\x02' + code + bytes(22) + sony + code + bytes(22 * 8)
    inventory = decode_inventory(fixture)
    order = order_from_inventory(inventory)
    assert order == [1, 0, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    assert deduplicate(order) == [1, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    print(f'PASS: {checks} actual ARM page writes/read-backs; other slots untouched')
    print('PASS: empty restore, maximum layout, sparse page IDs, malformed replies, inventory holes')
    print('Persistence/radio are modeled; real display refresh remains a live check.')


if __name__ == '__main__':
    main()
