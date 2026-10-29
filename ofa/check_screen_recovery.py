#!/usr/bin/env python3
"""Offline evidence for screen version, reset and update entry; NEVER Bluetooth.

Runs the original ARM routines with explicit peripheral/ROM hooks. It proves
which operations the application requests, not bootloader recovery behavior.
The screen bootloader is absent from the supplied image.
"""
import argparse
import json
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC

from check_touch_pages import FirmwareConfig
from firmware import STOCK_SHA256, sha256
from screen_fonts import BASE, check_stock


class MainRead(FirmwareConfig):
    def hook(self, cpu, address, size, extra):
        if address in (0x7E6A, 0x26C64):  # debug formatting and timer stop
            cpu.reg_write(UC_ARM_REG_R0, 0)
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))
            return
        super().hook(cpu, address, size, extra)


def main_version_path(image):
    """Screen reply -> main cached version -> final two W 02 inventory bytes."""
    for version in (0x013F, 0x1234, 0xFE01):
        config = MainRead(image)
        cpu = config.cpu
        # A successful screen opcode-02 response. Other status fields are zero.
        payload = b'\xa5\x5a\x02\x00\x00\x08' + version.to_bytes(2, 'big') + bytes(6)
        cpu.mem_write(0x20E000, payload)
        cpu.mem_write(0x20B4C4, struct.pack('<I', 0x20E800))
        for reg, value in ((UC_ARM_REG_R0, 0x20E000), (UC_ARM_REG_SP, 0x20F000),
                           (UC_ARM_REG_LR, 0x830001)):
            cpu.reg_write(reg, value)
        cpu.emu_start(0x81AAF5, 0x830000, count=20000)
        assert cpu.reg_read(UC_ARM_REG_PC) == 0x830000
        assert bytes(cpu.mem_read(0x20B166, 2)) == version.to_bytes(2, 'big')
        assert not config.writes
        reply = config.command(b'W\x02')
        assert reply[-2:] == version.to_bytes(2, 'big')
        assert not config.writes


def screen_trace(image, entry, payload_length=0):
    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(0, 0x40000)
    cpu.mem_write(BASE, image)
    cpu.mem_map(0x20000000, 0x10000)
    cpu.mem_write(0x200003B8, struct.pack('<I', 24000000))
    events = []
    reply = bytearray()
    functions = {
        0x1D218: 'payload_length', 0x1D386: 'reply_bytes',
        0x1D392: 'ack', 0x1D378: 'error', 0x1D40C: 'send_reply',
        0x4EDC: 'flash_init', 0x4FC4: 'flash_lock_set',
        0x4FE4: 'flash_mode', 0x5034: 'flash_sector_erase',
        0x50C0: 'flash_write8', 0x1EC5C: 'ui_event',
        0x19C9C: 'pre_reset', 0x684C: 'system_reset',
    }

    def hook(cpu, address, size, _):
        if address not in functions:
            return
        args = [cpu.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
        name = functions[address]
        if name == 'payload_length':
            value = payload_length
        else:
            event = dict(operation=name)
            if name in ('flash_init', 'flash_lock_set'):
                event['arguments'] = args[:2]
            elif name in ('flash_mode', 'flash_sector_erase', 'ui_event'):
                event['arguments'] = args[:1]
            elif name == 'flash_write8':
                event.update(address=args[0], data=bytes(cpu.mem_read(args[1], args[2])).hex())
            elif name == 'reply_bytes':
                data = bytes(cpu.mem_read(args[0], args[1]))
                reply.extend(data)
                event['data'] = data.hex()
            events.append(event)
            value = 0  # Modeled successful peripheral operation, NOT real flash.
        if name == 'system_reset':
            cpu.emu_stop()
            return
        cpu.reg_write(UC_ARM_REG_R0, value)
        cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))

    cpu.hook_add(UC_HOOK_CODE, hook)
    cpu.reg_write(UC_ARM_REG_SP, 0x20007000)
    cpu.reg_write(UC_ARM_REG_LR, 0x3F001)
    cpu.emu_start(entry | 1, 0x3F000, count=50000)
    assert cpu.reg_read(UC_ARM_REG_PC) in (0x3F000, 0x684C)
    return events, bytes(reply)


def main_reset_path(image):
    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    cpu.mem_map(0x800000, 0x40000)
    cpu.mem_write(0x80DE00, image)
    cpu.mem_map(0x200000, 0x10000)
    cpu.mem_map(0x70000, 0x1000)
    cpu.mem_map(0x40001000, 0x1000)
    cpu.mem_write(0x200158, struct.pack('<I', 0x70001))
    cpu.mem_write(0x40001000, struct.pack('<I', 0xFFFFFFFF))
    events = []

    def delay(cpu, address, size, _):
        if address == 0x70000:
            events.append(dict(operation='delay', argument=cpu.reg_read(UC_ARM_REG_R0)))
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))

    def write(cpu, access, address, size, value, _):
        if 0x40001000 <= address < 0x40002000:
            events.append(dict(operation='gpio_write', address=address, value=value))

    cpu.hook_add(UC_HOOK_CODE, delay)
    cpu.hook_add(UC_HOOK_MEM_WRITE, write)
    cpu.reg_write(UC_ARM_REG_SP, 0x20F000)
    cpu.reg_write(UC_ARM_REG_LR, 0x830001)
    cpu.emu_start(0x817B95, 0x830000, count=10000)
    assert cpu.reg_read(UC_ARM_REG_PC) == 0x830000
    assert events == [dict(operation='gpio_write', address=0x40001000, value=0xFFFFFFFE),
                      dict(operation='delay', argument=10),
                      dict(operation='gpio_write', address=0x40001000, value=0xFFFFFFFF)]
    return events


def capture_version(path):
    capture = json.loads(path.read_text())
    replies = []
    for transaction in capture.get('transactions', []):
        if bytes.fromhex(transaction.get('command', '')) != b'W\x02':
            continue
        reply = b''.join(bytes.fromhex(x) for x in transaction['notifications'])
        # URC7985: opcode + twelve 22-byte devices + twelve 20-byte activities + 7 trailer bytes.
        if len(reply) != 512 or reply[0] != 2:
            raise ValueError('Expected complete 512-byte URC7985 W 02 response')
        replies.append(int.from_bytes(reply[-2:], 'big'))
    if not replies or len(set(replies)) != 1:
        raise ValueError('Missing or inconsistent saved screen version')
    return replies[0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('main_stock', type=Path)
    p.add_argument('screen_stock', type=Path)
    p.add_argument('--capture', type=Path)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    main_image, screen = args.main_stock.read_bytes(), args.screen_stock.read_bytes()
    if sha256(main_image) != STOCK_SHA256:
        raise ValueError('Expected pinned stock main firmware 0.5.7')
    check_stock(screen)
    main_version_path(main_image)
    _, version_bytes = screen_trace(screen, 0x1C5B6)
    assert version_bytes == b'\x01\x3f'
    update, _ = screen_trace(screen, 0x1C5E0)
    flash = [e for e in update if e['operation'].startswith('flash_')]
    assert flash == [dict(operation='flash_init', arguments=[6, 1]),
                     dict(operation='flash_lock_set', arguments=[0, 2]),
                     dict(operation='flash_mode', arguments=[2]),
                     dict(operation='flash_sector_erase', arguments=[0xE00]),
                     dict(operation='flash_mode', arguments=[1]),
                     dict(operation='flash_write8', address=0xE50, data='55aa')]
    assert update[-1]['operation'] == 'system_reset'
    invalid, _ = screen_trace(screen, 0x1C5E0, payload_length=1)
    assert invalid == [dict(operation='error')]
    report = dict(status='OFFLINE EVIDENCE; RECOVERY NOT CONFIRMED',
                  main_sha256=sha256(main_image), screen_sha256=sha256(screen),
                  asset_version='013f', bootloader_in_asset=False,
                  update_entry=update, reset_entry=main_reset_path(main_image),
                  limitation='Peripheral operations modeled; no screen bootloader or hardware recovery executed.')
    if args.capture:
        version = capture_version(args.capture)
        report.update(saved_capture=str(args.capture), saved_screen_version=f'{version:04x}',
                      saved_version_matches_asset=(version == 0x013F))
    if args.output:
        with args.output.open('x') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
