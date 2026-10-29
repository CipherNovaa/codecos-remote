#!/usr/bin/env python3
"""Actual ARM label command, persistence boundaries, serializer and HID checks.

Validates the spare-keys candidate (the image to flash) against the INSTALLED
pause-playpause image, and proves the legacy CT01 chain still reproduces the
installed image byte-for-byte. All execution is offline Unicorn emulation.
"""
import argparse
import struct
from pathlib import Path

from check_touch_pages import FirmwareConfig
from check_touch_digits import execute_screen_key
from custom_firmware import ALIASES, LEGACY_ALIASES, SPARE_KEYS, build_custom_candidate
from firmware import button_map, sha256
from pause_playpause_firmware import build as build_installed
from spare_keys_firmware import INSTALLED_SHA256, SPARE_V1_SHA256, build as build_spare_keys
from touch_firmware import KEYS
from touch_pages import encode_pages

CT01_SHA256 = '1f5db4d75778e930f5c4b9653a2121a1991d8fada0d13219a584f78504364e64'


def setup(image, slot=1, profile=4, enabled=True):
    config = FirmwareConfig(image)
    config.cpu.mem_write(0x20A2A0 + 4 * slot + 0x17, bytes([profile << 4 | slot]))
    config.cpu.mem_write(0x20A2A8, struct.pack('<I', (1 << (slot - 1)) if enabled else 0))
    return config


def request(op, slot=1, key=0xFA, label=None):
    data = bytes([0x57, 0x0B, op, slot, key])
    return data if label is None else data + label.ljust(16, b'\0')


def serialize(config, slot, ordinal):
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP, UC_ARM_REG_LR
    cpu = config.cpu
    for reg, value in [(UC_ARM_REG_R0, slot), (UC_ARM_REG_R1, ordinal),
                       (UC_ARM_REG_R2, 0x20E400), (UC_ARM_REG_SP, 0x20F000), (UC_ARM_REG_LR, 0x830001)]:
        cpu.reg_write(reg, value)
    cpu.mem_write(0x20E400, b'\xa5' * 256)
    config.writes = []
    cpu.emu_start(0x815321, 0x830000, count=10000)
    length = cpu.reg_read(UC_ARM_REG_R0)
    assert not config.writes
    assert bytes(cpu.mem_read(0x20E400 + length, 256 - length)) == b'\xa5' * (256 - length)
    return bytes(cpu.mem_read(0x20E400, length))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stock', type=Path)
    args = p.parse_args()
    stock = args.stock.read_bytes()
    legacy, _ = build_custom_candidate(stock)
    assert sha256(legacy) == CT01_SHA256, 'legacy CT01 build drifted'
    before, _ = build_installed(stock)
    assert sha256(before) == INSTALLED_SHA256, 'installed chain drifted'
    v1, _ = build_spare_keys(stock, 1)
    assert sha256(v1) == SPARE_V1_SHA256, 'flashed spare-keys-v1 build drifted'
    image, manifest = build_spare_keys(stock)
    assert button_map(image) == button_map(before) == button_map(v1)
    assert len(manifest['changes_from_spare_v1']) - 32 in range(1, 13), manifest['changes_from_spare_v1']
    print('PASS: legacy CT01, installed pause-playpause and flashed spare-keys-v1 images reproduce their pinned SHA-256')

    pool_of = {k['alias']: k['pool'] for k in SPARE_KEYS}
    pool_of.update({0xFA + i: k['pool'] for i, k in enumerate(KEYS)})
    pool_of.update({k['logical']: k['pool'] for k in KEYS})
    usages = {k['alias']: k['usage'] for k in SPARE_KEYS}
    usages.update({0xFA + i: k['usage'] for i, k in enumerate(KEYS)})
    originals = {k['logical']: k['usage'] for k in KEYS}
    changes = usages | originals
    assert set(usages) == set(range(0xF0, 0x100)) and set(ALIASES.values()) == set(usages)
    assert len(set(changes.values())) == 16, 'HID usages must be distinct'
    legacy_digits = {0xF0 + i: (0x27 if i == 0 else 0x1D + i) for i in range(10)}
    for profile in (1, 2, 3, 4):
        for enabled in (False, True):
            for key in range(256):
                options = dict(slot=12, profile=profile, ble_enabled=enabled, selected_slot=1)
                actual = execute_screen_key(image, key, **options)
                if enabled and profile == 4 and key in changes:
                    assert actual == ([(10, bytes([0, 0, changes[key], 0, 0, 0, 0, 0])), (10, bytes(8))], [])
                    if key in legacy_digits:  # the installed image sent a digit here
                        assert execute_screen_key(before, key, **options) == \
                            ([(10, bytes([0, 0, legacy_digits[key], 0, 0, 0, 0, 0])), (10, bytes(8))], [])
                else:
                    assert actual == execute_screen_key(before, key, **options), (profile, enabled, key)
    print('PASS: 2,048 original/alias touchscreen dispatch cases versus the installed image; physical map unchanged')
    for key in changes:
        for overrides in ({'mover': True}, {'macro': True}):
            assert execute_screen_key(image, key, **overrides) == ([], [])
        for connected, active in ((False, 1), (True, 4)):
            calls = []
            reports, ir = execute_screen_key(image, key, connected=connected,
                                             connected_slot=active, events=calls)
            assert not reports and not ir
            assert [a for a, _ in calls] == [0xB918, 0x817984 if connected else 0x817938]
            if not connected:
                # Original wake handler takes the selected pool index in r1.
                assert calls[-1][1][:3] == (1, pool_of[key], 5), (hex(key), calls)
    print('PASS: macro/key-mover precedence and existing wake/switch dispatch retained for all 16 aliases')

    spare_aliases = [k['alias'] for k in SPARE_KEYS]
    for slot in range(1, 13):
        c = setup(image, slot)
        assert c.command(b'W\x0b\x00') == b'\x0b\x01CT01' and not c.writes
        # Retain an unrelated IR slot's custom label while editing this slot.
        other = 2 if slot != 2 else 3
        saved = bytes([other, 0xF0]) + b'IR LABEL'.ljust(16, b'\0')
        c.cpu.mem_write(0x20BE54, saved)
        c.cpu.mem_write(0x20A2A7, b'\x01')
        metadata = bytes(c.cpu.mem_read(0x20A2A0, 0x60))
        for alias in ALIASES.values():
            label = f'TEST {alias:02X}'.encode()
            assert c.command(request(1, slot, alias, label)) == b'\x0b\x01'
            assert [w[0] for w in c.writes] == [0x2230, 4]
            assert [len(w[1]) for w in c.writes] == [0x900, 0x60]
            changed_meta = bytes(c.cpu.mem_read(0x20A2A0, 0x60))
            assert changed_meta[:7] + changed_meta[8:] == metadata[:7] + metadata[8:]
            assert bytes(c.cpu.mem_read(0x20BE54, 18)) == saved
            assert c.command(request(3, slot, alias)) == b'\x0b\x01\x01' + label.ljust(16, b'\0')
            assert not c.writes
        # Label changes do not require learning or changing the device record.
        assert c.command(request(1, slot, 0xFA, b'SUBS')) == b'\x0b\x01'
        assert c.command(encode_pages(slot, [{'id': 1, 'layout': 0xFF, 'keys': [0xFA]}])) == b'\x17\x01'
        assert serialize(c, slot, 1) == b'\xff\x01\xfa' + b'SUBS'.ljust(16, b'\0')
        # A full six-key spare page (the labels.py layout) serializes every label.
        for i, alias in enumerate(spare_aliases[:6]):
            assert c.command(request(1, slot, alias, f'SPARE {i + 1}'.encode())) == b'\x0b\x01'
        assert c.command(encode_pages(slot, [{'id': 3, 'layout': 0xFF, 'keys': spare_aliases[:6]}])) == b'\x17\x01'
        expected = b'\xff\x06' + b''.join(bytes([a]) + f'SPARE {i + 1}'.encode().ljust(16, b'\0')
                                          for i, a in enumerate(spare_aliases[:6]))
        assert serialize(c, slot, 1) == expected and len(expected) == 104
        assert c.command(request(2, slot, 0xFA)) == b'\x0b\x01'
        assert c.command(request(3, slot, 0xFA)) == b'\x0b\x01\x00' + bytes(16)
        assert c.command(request(3, slot, 0xFB)) == b'\x0b\x01\x01' + b'TEST FB'.ljust(16, b'\0')
    print('PASS: labels set/read/update/delete for all 16 aliases in all slots; exact FTL bounds; other labels and records retained')
    print('PASS: original ARM serializer supplies independent labels for alias FA and a six-key SPARE page (104 bytes)')

    # Global label table: 16 aliases on both type-4 slots plus a full third slot still fit 128.
    c = setup(image, 1)
    c.cpu.mem_write(0x20A2A0 + 4 * 5 + 0x17, bytes([4 << 4 | 5]))
    c.cpu.mem_write(0x20A2A8, struct.pack('<I', (1 << 0) | (1 << 4)))
    for slot in (1, 5):
        for alias in ALIASES.values():
            assert c.command(request(1, slot, alias, b'X')) == b'\x0b\x01'
    assert c.cpu.mem_read(0x20A2A7, 1) == b'\x20'
    print('PASS: 32 label records (16 per type-4 slot) within the 128-record global table')

    valid = request(1, label=b'ABCDEFGHIJKLMNO')
    # Lengths below two bytes never select this opcode in the stock dispatcher.
    invalid = [valid[:n] for n in range(2, len(valid))]
    invalid += [request(1, label=b'ABCDEFGHIJKLMNOP'), valid + b'X', request(1, label=b'A\x00B'), request(1, label=b'\xff'),
                request(1, label=b''), request(1, key=0xEF, label=b'X'),
                request(1, slot=0, label=b'X'), request(1, slot=13, label=b'X')]
    c = setup(image)
    for payload in invalid:
        assert c.command(payload) != b'\x0b\x01', payload.hex()
        assert not c.writes, payload.hex()
    for profile, enabled in [(1, False), (1, True), (3, True), (4, False)]:
        c = setup(image, profile=profile, enabled=enabled)
        assert c.command(valid) == b'\x0b\x02' and not c.writes
    c = setup(image)
    c.cpu.mem_write(0x20A2A7, b'\x80')
    assert c.command(valid) == b'\x0b\x03' and not c.writes
    c.cpu.mem_write(0x20A2A7, b'\x81')
    assert c.command(valid) == b'\x0b\x03' and not c.writes
    c = setup(image)
    assert c.command(valid) == b'\x0b\x01'
    assert c.command(request(3)) == b'\x0b\x01\x01ABCDEFGHIJKLMNO\x00'
    print('PASS: malformed lengths/labels/slots, IR profiles, full/corrupt table; 15-byte labels plus NUL')
    assert set(LEGACY_ALIASES.values()) == set(ALIASES.values())
    print(manifest['candidate']['sha256'])
    print('Offline only: real display rendering and boot/radio still require live verification.')


if __name__ == '__main__':
    main()
