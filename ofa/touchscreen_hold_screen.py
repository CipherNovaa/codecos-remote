#!/usr/bin/env python3
"""Build the recoverable screen-MCU half of touchscreen CAST/RESUME hold.

Offline only: this writes a candidate under artifacts/ and never contacts or
flashes a remote.  The candidate remains exactly the installed v15 image size.
"""
import hashlib
import json
import struct
from pathlib import Path

from screensaver_firmware import (ROOT, assemble_stable, armv6m_listing_ok,
                                  assert_bl, capstone_ok, sha256)

BASE = ROOT / 'artifacts/screensaver-v15/screen-screensaver-v15-NOREARM-DO-NOT-FLASH.bin'
BASE_SHA = '64ec0c29e2c91a5623276b4ffb9f40cf86a2bf2f09c80714cc3f34130e11ed73'
OUT_DIR = ROOT / 'artifacts/touchscreen-hold-screen'
SCREEN_VA = 0x1000
IMAGE_BYTES = 180608
ENTRY = 0x29e54
LIMIT = 0x29fa9                         # 341 byte zero run, exclusive
CALLBACK_POINTERS = ((0x1c34, 'ss_key', 0x87c5), (0x307c, 'ss_key', 0x87c5),
                     (0x9e3c, 'ss_key_num', 0xa681), (0x2480, 'ss_key_table', 0xc235),
                     (0xa088, 'ss_key_grp', 0x5b89))
FILTER_SITES = (0x38a8, 0x9e22, 0x1d94, 0x9f5c, 0xa01a)


def va_off(va):
    return va - SCREEN_VA


def check_base(base):
    if len(base) != IMAGE_BYTES:
        raise ValueError(f'screen base size is {len(base)}, expected {IMAGE_BYTES}')
    if sha256(base) != BASE_SHA:
        raise ValueError('screen base SHA does not match the v15 NOREARM handoff base')
    for va in FILTER_SITES:
        if base[va_off(va):va_off(va) + 2] != bytes.fromhex('0722'):
            raise ValueError(f'event-filter site {va:#x} is not `movs r2, #7`')
    for va, _, stock in CALLBACK_POINTERS:
        if struct.unpack_from('<I', base, va_off(va))[0] != stock:
            raise ValueError(f'callback pointer {va:#x} is not stock {stock:#x}')
    if any(base[va_off(ENTRY):va_off(LIMIT)]):
        raise ValueError('selected screen extension range is not all zero padding')


def build(base):
    check_base(base)
    blobs, syms = assemble_stable('cortex-m0plus', 'touchscreen_hold_screen.ld', 'ss_key',
                                  ['touchscreen_hold_screen.S', 'touchscreen_hold_screen.c'], sections=('.text', '.grp', '.aux'))
    code, grp, aux = blobs['.text'], blobs['.grp'], blobs['.aux']
    if syms['ss_key'] != ENTRY:
        raise ValueError('screen entry VA moved')
    if len(code) > LIMIT - ENTRY or len(grp) > 0x12a or len(aux) > 0x134:
        raise ValueError(f'extension sizes text={len(code)} grp={len(grp)} aux={len(aux)} exceed a verified run')
    armv6m_listing_ok(__import__('screensaver_firmware').LISTINGS['ss_key'])
    capstone_ok(code, ENTRY, {'ss_key': syms['ss_key'], 'ss_key_num': syms['ss_key_num'],
                              'ss_key_table': syms['ss_key_table'], 'ss_key_impl': syms['ss_key_impl'],
                              'ss_key_num_impl': syms['ss_key_num_impl'], 'ss_key_table_impl': syms['ss_key_table_impl']}, armv6m=True)
    capstone_ok(grp, syms['ss_key_grp'], {'ss_key_grp': syms['ss_key_grp'], 'ss_key_grp_impl': syms['ss_key_grp_impl']}, armv6m=True)

    out = bytearray(base)
    allowed = set(range(va_off(ENTRY), va_off(ENTRY) + len(code)))
    out[va_off(ENTRY):va_off(ENTRY) + len(code)] = code
    out[va_off(syms['ss_key_grp']):va_off(syms['ss_key_grp']) + len(grp)] = grp
    allowed.update(range(va_off(syms['ss_key_grp']), va_off(syms['ss_key_grp']) + len(grp)))
    out[va_off(syms['ss_key_impl']):va_off(syms['ss_key_impl']) + len(aux)] = aux
    allowed.update(range(va_off(syms['ss_key_impl']), va_off(syms['ss_key_impl']) + len(aux)))
    for va, symbol, _ in CALLBACK_POINTERS:
        struct.pack_into('<I', out, va_off(va), syms[symbol] | 1)
        allowed.update(range(va_off(va), va_off(va) + 4))
    for va in FILTER_SITES:
        out[va_off(va):va_off(va) + 2] = bytes.fromhex('0022')
        allowed.update(range(va_off(va), va_off(va) + 2))
    out = bytes(out)

    changed = {i for i, (a, b) in enumerate(zip(base, out)) if a != b}
    if changed - allowed:
        raise ValueError('change outside the screen patch allowlist')
    if len(out) != IMAGE_BYTES:
        raise ValueError('screen image size changed')
    for va, symbol, _ in CALLBACK_POINTERS:
        if struct.unpack_from('<I', out, va_off(va))[0] != syms[symbol] | 1:
            raise ValueError(f'callback pointer {va:#x} did not repoint')
    for va in FILTER_SITES:
        if out[va_off(va):va_off(va) + 2] != bytes.fromhex('0022'):
            raise ValueError(f'event filter {va:#x} did not become LV_EVENT_ALL')
    return out, {'base_sha256': sha256(base), 'candidate_sha256': sha256(out),
                 'bytes': len(out), 'handler_va': hex(ENTRY), 'handler_bytes': len(code), 'group_handler_bytes': len(grp),
                 'callback_pointers': {hex(va): hex(syms[symbol] | 1) for va, symbol, _ in CALLBACK_POINTERS},
                 'filter_sites': [hex(x) for x in FILTER_SITES], 'changed_bytes': len(changed),
                 'shared_0x3764_callbacks': ['0x87c5 at pools 0x1c34 and 0x307c; both dispatch through 0x8f1b'],
                 'status': 'OFFLINE CANDIDATE — NOT FLASHED; covers all four confirmed live registrations'}


def emulate_edges(image):
    """Run the new handler with the real assembled code and ABI-shaped stubs."""
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0,
                                   UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                   UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_SP)
    calls = {0x12210: 'event', 0x1225a: 'target', 0x1225e: 'numeric', 0x9b58: 'group',
             0x172fa: 'index', 0x11e4: 'copy', 0x1bf50: 'send'}

    def run(edge, key=0xf0, *, numeric=False, table=False, group=False, phase=0,
            record_type=2, record_slot=3, grid_count=4, audit=False):
        cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        cpu.mem_map(0, 0x40000)
        cpu.mem_write(SCREEN_VA, image)
        cpu.mem_map(0x20000000, 0x10000)
        cpu.mem_write(0x200003f4, struct.pack('<I', phase))
        cpu.mem_write(0x20000005, b'\x07')
        cpu.mem_write(0x20000259, bytes([grid_count]))
        entry_sp = 0x2000f000
        record = bytearray(0x14); record[0] = record_type; record[1] = record_slot; record[2] = key
        cpu.mem_write(0x20000000 + 2 * 0x14 + 0x1ae, bytes(record))
        cpu.mem_write(0x2000025a + 2, b'\x3d')
        cpu.reg_write(UC_ARM_REG_SP, entry_sp)
        cpu.reg_write(UC_ARM_REG_LR, 0x2a001)
        cpu.reg_write(UC_ARM_REG_R0, 0x20008000)
        cpu.reg_write(UC_ARM_REG_R4, 0x44)
        cpu.reg_write(UC_ARM_REG_R5, 0x55)
        sent, stock_calls = [], []
        trace = {'target': 0, 'numeric': 0, 'group': 0, 'copy': 0}
        before = bytes(cpu.mem_read(0x20000000, 0x21))
        def hook(cpu, address, size, _):
            if address == 0x2a000:
                cpu.emu_stop(); return
            if address in (0x87c4, 0xa680, 0xc234):
                stock_calls.append(1)
                cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))
                return
            which = calls.get(address)
            if which == 'event': cpu.reg_write(UC_ARM_REG_R0, edge)
            elif which == 'target':
                trace['target'] += 1; cpu.reg_write(UC_ARM_REG_R0, 0x20008100)
            elif which == 'index': cpu.reg_write(UC_ARM_REG_R0, 2)
            elif which == 'numeric':
                trace['numeric'] += 1; cpu.reg_write(UC_ARM_REG_R0, 2)
            elif which == 'group':
                trace['group'] += 1; cpu.reg_write(UC_ARM_REG_R0, 0x3e)
            elif which == 'copy':
                trace['copy'] += 1
                cpu.mem_write(cpu.reg_read(UC_ARM_REG_R0), bytes(cpu.mem_read(cpu.reg_read(UC_ARM_REG_R1), 0x14)))
            elif which == 'send':
                sent.append(tuple(cpu.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)))
            else: return
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))
        cpu.hook_add(UC_HOOK_CODE, hook)
        start = (struct.unpack_from('<I', image, va_off(0xa088))[0] if group else
                 struct.unpack_from('<I', image, va_off(0x9e3c))[0] if numeric else
                 struct.unpack_from('<I', image, va_off(0x2480))[0] if table else ENTRY) | 1
        cpu.emu_start(start, 0x2a000, count=300)
        if cpu.reg_read(UC_ARM_REG_SP) != entry_sp:
            raise AssertionError('ss_key did not return with its entry stack pointer')
        if (cpu.reg_read(UC_ARM_REG_R4), cpu.reg_read(UC_ARM_REG_R5)) != (0x44, 0x55):
            raise AssertionError('ss_key did not preserve callee-saved registers')
        result = sent, stock_calls, cpu.mem_read(0x20000005, 1)[0]
        if audit:
            return result, before, bytes(cpu.mem_read(0x20000000, 0x21)), trace
        return result
    assert run(1) == ([(3, 0xf0, 1, 1)], [], 3)
    assert run(8) == ([(3, 0xf0, 1, 0)], [], 3)
    assert run(3) == ([(3, 0xf0, 1, 0)], [], 3)
    # CLICKED is consumed: real press/up edges already sent the key.
    assert run(7) == ([], [], 3)
    assert run(1, record_type=5) == ([(0, 0xf0, 1, 1)], [], 0)
    # Favorites oracle: types 0/1/>5 always reach stock, while types 2--5
    # own both edge reports and CLICKED.  Slots deliberately begin at 7.
    for typ in (0, 1, 6):
        for edge in (1, 8, 3, 7):
            assert run(edge, record_type=typ) == ([], [1], 7)
    for typ in (2, 3, 4):
        assert run(1, record_type=typ) == ([(3, 0xf0, 1, 1)], [], 3)
        assert run(8, record_type=typ) == ([(3, 0xf0, 1, 0)], [], 3)
        assert run(3, record_type=typ) == ([(3, 0xf0, 1, 0)], [], 3)
        assert run(7, record_type=typ) == ([], [], 3)
    for edge, byte8 in ((1, 1), (8, 0), (3, 0)):
        assert run(edge, record_type=5) == ([(0, 0xf0, 1, byte8)], [], 0)
    assert run(7, record_type=5) == ([], [], 0)

    # Both "more keys" pages use the authoritative connection slot [5].
    for phase in (0xb, 0x15):
        assert run(1, phase=phase) == ([(7, 0x3d, 1, 1)], [], 7)
        assert run(8, phase=phase) == ([(7, 0x3d, 1, 0)], [], 7)
        assert run(3, phase=phase) == ([(7, 0x3d, 1, 0)], [], 7)
        assert run(7, phase=phase) == ([], [], 7)
        assert run(1, phase=phase, grid_count=2) == ([], [1], 7)
    # The numeric callback emits the same edges with its distinct resolver.
    assert run(1, numeric=True) == ([(7, 0x0f, 1, 1)], [], 7)
    assert run(8, numeric=True) == ([(7, 0x0f, 1, 0)], [], 7)
    assert run(1, table=True) == ([(7, 0x3d, 1, 1)], [], 7)
    assert run(3, table=True) == ([(7, 0x3d, 1, 0)], [], 7)
    assert run(1, group=True) == ([(7, 0x3e, 1, 1)], [], 7)
    assert run(8, group=True) == ([(7, 0x3e, 1, 0)], [], 7)
    # An unsupported 0x87c4 phase must tail-call stock and stock must return to the wrapper's
    # original caller, not back into the wrapper's post-BL address.
    assert run(7, phase=1) == ([], [1], 7)
    # LV_EVENT_ALL is a firehose.  Every handler must tail-call stock before
    # resolution or any SCREEN_STATE write for non-input event codes.
    for edge in (2, 4, 5, 6, 0x16, 0x1d):
        for flags in ({}, {'numeric': True}, {'table': True}, {'group': True}):
            result, before, after, trace = run(edge, audit=True, **flags)
            # ss_key/num retain stock tails; table/group are direct callbacks
            # whose stock registrations had no fallback path.
            expected_stock = [] if ('table' in flags or 'group' in flags) else [1]
            assert result == ([], expected_stock, 7), (edge, flags, result)
            assert before == after, (edge, flags, 'non-input event wrote SCREEN_STATE')
            assert not any(trace.values()), (edge, flags, trace)


def main():
    base = BASE.read_bytes()
    image, report = build(base)
    emulate_edges(image)
    OUT_DIR.mkdir(exist_ok=True)
    output = OUT_DIR / 'screen-touchscreen-hold-all-keys-DO-NOT-FLASH.bin'
    output.write_bytes(image)
    (OUT_DIR / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
