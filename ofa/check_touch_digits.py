#!/usr/bin/env python3
"""Execute the existing screen-key-to-HID path offline, without Bluetooth.

Uses the actual 0.5.7 ARM handler and HID report builder in Unicorn. Models an
already connected device, no macros/key movers, and enabled keyboard reports.
Logging, screen acknowledgments, indicator hardware and radio submission are
stubbed. This does not establish that a keypad is visible on the real screen.
"""
import argparse
import struct
from pathlib import Path

from firmware import STOCK_SHA256, build_candidate, sha256, verify_image


def execute_screen_key(data, logical_key, *, slot=1, profile=4, ble_enabled=True,
                       selected_slot=None, connected=True, connected_slot=None,
                       mover=False, macro=False, events=None, byte8=1,
                       activity_mode=False, activity=1):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                   UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)

    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    cpu.mem_map(0, 0x80000)  # Hooked ROM calls; ROM contents are not supplied.
    cpu.mem_map(0x800000, 0x40000)
    cpu.mem_write(0x80DE00, data)
    cpu.mem_map(0x200000, 0x10000)

    def byte(address, value):
        cpu.mem_write(address, bytes([value]))

    def word(address, value):
        cpu.mem_write(address, struct.pack('<I', value))

    byte(0x20A301, 12)  # Model supports twelve device slots.
    byte(0x20A2A0, (slot if selected_slot is None else selected_slot) | (activity << 4 if activity_mode else 0))
    byte(0x20A2A0 + 4 * slot + 0x17, profile << 4)
    word(0x20A2A4, (slot if connected_slot is None else connected_slot) << 16)
    word(0x20A2A8, (1 << (slot - 1)) if ble_enabled else 0)
    word(0x20B448, (1 << 15) if connected else 0)
    byte(0x20B451, int(activity_mode))
    if activity_mode:
        byte(0x20B4C4 + activity * 4 - 1, 2)  # activity's IR target slot
    byte(0x20B0B0 + 0x15, 4)  # Send immediately, then release.
    byte(0x20B0B0 + 1, 1)  # Keyboard reports enabled.
    byte(0x20B0B0 + 4, 1)  # Consumer reports enabled.

    # Internal screen request, NOT a GATT W command. Header validation occurs
    # upstream at 0x81b812; this starts at the subsequent command dispatcher.
    frame = bytes([0xA5, 0x5A, 0x43, 0, 3, 0, slot, logical_key, byte8, 0])
    cpu.mem_write(0x20E000, frame)
    cpu.reg_write(UC_ARM_REG_SP, 0x20F000)
    cpu.reg_write(UC_ARM_REG_LR, 0x830001)
    cpu.reg_write(UC_ARM_REG_R0, 0x20E000)

    # Explicit boundaries: logging, wake, screen ACK, key-mover/macro checks,
    # LED/buzzer control. No key mapping, branch or report construction stub.
    zero_stubs = {0x7A5E, 0x816EEE, 0x81B960, 0x81CF64, 0x815EDA,
                  0x81BC28, 0x81BB9A, 0x81BBBA, 0x81BBDA,
                  0x817938, 0x817984, 0xB918}
    reports = []
    infrared = []
    finished = False

    def hook(cpu, address, size, _):
        nonlocal finished
        if address in (0x817938, 0x817984, 0xB918) and events is not None:
            events.append((address, tuple(cpu.reg_read(r) for r in
                          (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))))
        if address == 0x81B1FC:
            finished = True
            cpu.emu_stop()
            return
        if address == 0x81D082:
            # Stop before IR implementation/hardware; record its dispatch.
            infrared.append(logical_key)
        elif address == 0x4F7A4:
            report = bytes(cpu.mem_read(cpu.reg_read(UC_ARM_REG_R3), 8))
            reports.append((cpu.reg_read(UC_ARM_REG_R2), report))
        elif address < 0x800000 and address not in zero_stubs:
            raise AssertionError(f'Unexpected external call {address:#x}')
        if address in zero_stubs or address in (0x4F7A4, 0x81D082):
            # The caller tests this as the IR dispatch's handled result.  A
            # successful IR dispatch is non-zero; this also verifies the
            # trampoline's release path returns the same control-flow shape.
            result = (address == 0x81D082 or
                      (address == 0x81CF64 and mover) or (address == 0x815EDA and macro))
            cpu.reg_write(UC_ARM_REG_R0, int(result))
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))

    cpu.hook_add(UC_HOOK_CODE, hook)
    cpu.emu_start(0x81AD61, 0x830000, count=3000)
    assert finished, 'Handler did not reach its completion branch'
    return reports, infrared


def check_touchscreen_hold_main():
    """Gate the exact 0x43-frame edge semantics of the in-place v16 patch."""
    from touchscreen_hold_main import B, build_inplace

    candidate = build_inplace(B.read_bytes())
    usage = 0x6c  # logical 0x2f (BLUE / CAST) is profile-4 F17.
    down, ir = execute_screen_key(candidate, 0x2f, byte8=1)
    assert down == [(0x0a, bytes([0, 0, usage, 0, 0, 0, 0, 0]))] and not ir, (down, ir)
    up, ir = execute_screen_key(candidate, 0x2f, byte8=0)
    assert up == [(0x0a, bytes(8))] and not ir, (up, ir)

    # Unmapped/IR keys must dispatch exactly once on a down edge and never on
    # its up edge.  These are the calls redirected through 0x827416.
    for key in (0x22, 0x36):
        reports, ir = execute_screen_key(candidate, key, byte8=1)
        assert not reports and ir == [key], (key, reports, ir)
        reports, ir = execute_screen_key(candidate, key, byte8=0)
        assert not reports and not ir, (key, reports, ir)

    # An activity-mode BLUE release can be remapped to the IR path; the gate
    # must still suppress it.  Slot 1 is BLE-connected, activity 1 routes to
    # slot 2 in this model (the same branch shape as the real handler).
    reports, ir = execute_screen_key(candidate, 0x2f, byte8=0,
                                     activity_mode=True)
    assert not ir, (reports, ir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    args = parser.parse_args()
    stock = args.stock.read_bytes()
    verify_image(stock)
    if sha256(stock) != STOCK_SHA256:
        raise ValueError('Offsets apply only to the saved stock 0.5.7 asset')
    candidate, _ = build_candidate(stock)
    count = 0
    for image in (stock, candidate):
        for slot in (1, 5):
            for digit in range(10):
                usage = 0x27 if digit == 0 else 0x1D + digit
                actual, ir = execute_screen_key(image, 0x0D + digit, slot=slot)
                expected = bytes([0, 0, usage, 0, 0, 0, 0, 0])
                assert actual == [(0x0A, expected), (0x0A, bytes(8))], (digit, actual)
                assert not ir
                count += 1
            for key in (0x0C, 0x17, 0x19):  # Outside the numeric range.
                reports, ir = execute_screen_key(image, key, slot=slot)
                assert not reports and ir == [key]
            reports, ir = execute_screen_key(image, 0x0D, slot=slot,
                                             profile=1, ble_enabled=False)
            assert not reports and ir == [0x0D]
    print(f'PASS: {count} screen digit events built correct HID press/release reports')
    print('PASS: non-digit events and an IR-only slot retained the IR branch')
    print('Both stock and the existing 24-button candidate contain this path.')
    check_touchscreen_hold_main()
    print('PASS: touchscreen-hold main sends HID down/zero edges and gates IR to byte8=1.')
    print('MODELED STATE ONLY: real touchscreen layout and live BLE delivery remain untested.')


if __name__ == '__main__':
    main()
