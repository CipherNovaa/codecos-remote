#!/usr/bin/env python3
"""Offline validation of the main "blue light bar off" candidate (blue_led_off_firmware.py).

Static: base provenance (pinned main v1 artifact == from-source rebuild == the SHA the
remote validated on 2026-09-23), Realtek image check, exact diff (four 4-byte sites +
digest), capstone disassembly of every site before/after, named regions identical.

Behavioural (unicorn, Cortex-M4): runs the five LED paths in BOTH images with the LED
API entry points instrumented.  In the candidate the LED2 ON/BLINK calls must vanish
from the key-hold, key-blink, IR-start and touch paths while each path's other effects
(LED OFF calls, the 0x20b448 |= 0x20 state update, the stack/return of the tail call)
stay identical to main v1, and the pairing blink must still call BLINK for LED0 and LED2.

Run in the venv: artifacts/venv/bin/python check_blue_led_off.py [candidate.bin]
"""
import json
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_ARCH_ARM, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R11, UC_ARM_REG_SP)

from blue_led_off_firmware import (LED_BLINK, LED_OFF_A, LED_OFF_B, LED_ON, MAIN_V1_SHA, NAMED_REGIONS, PRESERVED,
                                   ROOT, SITES, build, check_sites, disasm_span, flash_journal_proof, load_main_v1,
                                   reproduce_candidate, reproduce_main_v1)
from firmware import SHA_OFFSET, verify_image
from screensaver_firmware import MAIN_VA, sha256

STOCK = ROOT / 'artifacts/ble24/stock-0.5.7.bin'
DEFAULT = ROOT / 'artifacts/codecos-v16/main-codecos-v16-DO-NOT-FLASH.bin'

LED_API = {LED_ON: 'ON', LED_OFF_A: 'OFF', LED_BLINK: 'BLINK', LED_OFF_B: 'OFF2'}
RAM, RAM_SIZE = 0x200000, 0x10000          # 0x20b448 / 0x20da90 / 0x20dbe4 live here
STACK = 0x20f000
SENTINEL = 0x2ffffe                        # lr for whole-function runs (never executed)


class Emu:
    def __init__(self, image):
        self.cpu = cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        cpu.mem_map(0x800000, 0x30000)
        cpu.mem_write(MAIN_VA, image)
        cpu.mem_map(RAM, RAM_SIZE)
        cpu.mem_map(0x2f0000, 0x10000)
        self.log = []
        cpu.hook_add(UC_HOOK_CODE, self._hook, begin=LED_ON, end=LED_OFF_B + 2)

    def _hook(self, cpu, address, size, _):
        if address in LED_API:
            regs = [cpu.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
            # ON/OFF take only the channel (r0); BLINK takes (channel, on_ms, off_ms, count)
            self.log.append((LED_API[address], *regs) if address == LED_BLINK else (LED_API[address], regs[0]))
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR) | 1)   # stub: return immediately

    def run(self, start, until, *, regs=(), stack=(), lr=SENTINEL, count=2000):
        cpu = self.cpu
        cpu.mem_write(RAM, bytes(RAM_SIZE))
        sp = STACK - 4 * len(stack)
        for i, w in enumerate(stack):
            cpu.mem_write(sp + 4 * i, struct.pack('<I', w))
        cpu.reg_write(UC_ARM_REG_SP, sp)
        cpu.reg_write(UC_ARM_REG_LR, lr | 1)
        for r, v in regs:
            cpu.reg_write(r, v)
        self.log = []
        cpu.emu_start(start | 1, until, count=count)
        return list(self.log), cpu.reg_read(UC_ARM_REG_PC)

    def word(self, a):
        return struct.unpack('<I', self.cpu.mem_read(a, 4))[0]


def behavioural(v1, cand):
    a, b = Emu(v1), Emu(cand)
    results = {}

    # 1. key handler, held-key branch 0x81cb74 (r4 = key != 0): OFF 0,1,2 then LED2 ON
    la, _ = a.run(0x81cb74, 0x81cb8e, regs=[(UC_ARM_REG_R4, 5)])
    lb, _ = b.run(0x81cb74, 0x81cb8e, regs=[(UC_ARM_REG_R4, 5)])
    assert la == [('OFF2', 0), ('OFF2', 1), ('OFF2', 2), ('ON', 2)], la
    assert lb == la[:3], lb
    results['key_hold_steady_on'] = {'v1': la, 'candidate': lb}

    # 2. key handler, blink variant 0x81cb66: BLINK(2, 200, 200, 1)
    la, _ = a.run(0x81cb66, 0x81cb8e)
    lb, _ = b.run(0x81cb66, 0x81cb8e)
    assert la == [('BLINK', 2, 0xc8, 0xc8, 1)], la
    assert lb == [], lb
    results['key_blink_200ms'] = {'v1': la, 'candidate': lb}

    # 3. IR-transmit start hook 0x81610a, whole function, idle state (RAM zero):
    #    OFF 0,1,2 then LED2 ON, then 0x20b448 |= 0x20 and return.  The state update
    #    and the return must survive the NOP.
    la, pa = a.run(0x81610a, SENTINEL)
    lb, pb = b.run(0x81610a, SENTINEL)
    assert la == [('OFF2', 0), ('OFF2', 1), ('OFF2', 2), ('ON', 2)], la
    assert lb == la[:3], lb
    assert pa == SENTINEL and pb == SENTINEL, (hex(pa), hex(pb))
    assert a.word(0x20b448) == 0x20 and b.word(0x20b448) == 0x20
    results['ir_start_hook'] = {'v1': la, 'candidate': lb, 'state_0x20b448_after': hex(b.word(0x20b448))}

    # 3b. same hook with bit 12 of 0x20b448 set (stock lights LED0 instead): the NOP drops
    #     that ON as well (documented as note_0x81614e in the manifest); the state update survives
    def with_bit12(e):
        e.cpu.mem_write(RAM, bytes(RAM_SIZE))
        e.cpu.mem_write(0x20b448, struct.pack('<I', 0x1000))
        e.log = []
        e.cpu.reg_write(UC_ARM_REG_SP, STACK)
        e.cpu.reg_write(UC_ARM_REG_LR, SENTINEL | 1)
        e.cpu.emu_start(0x81610a | 1, SENTINEL, count=2000)
        return list(e.log), e.word(0x20b448)
    la, wa = with_bit12(a)
    lb, wb = with_bit12(b)
    assert la == [('OFF2', 0), ('OFF2', 1), ('OFF2', 2), ('ON', 0)], la
    assert lb == la[:3] and wa == wb == 0x1020, (lb, hex(wb))
    results['ir_start_hook_bit12_led0_variant'] = {'v1': la, 'candidate': lb, 'state_after': hex(wb)}

    # 4. touch blink tail call: from 0x81d498 (movs r2,#0xfa; movs r3,#1; pop.w {r4,lr}; mov r1,r2;
    #    movs r0,#2; b.w BLINK).  Stack = [r4', lr'].  v1: BLINK(2,250,250,1) then returns to lr'.
    #    candidate: `bx lr` returns to lr' with r4 restored and no BLINK.
    stack = (0x44444444, 0x2ffff0 | 1)
    la, pa = a.run(0x81d498, 0x2ffff0, stack=stack, lr=0x11111110)
    lb, pb = b.run(0x81d498, 0x2ffff0, stack=stack, lr=0x11111110)
    assert la == [('BLINK', 2, 0xfa, 0xfa, 1)], la
    assert lb == [], lb
    assert pa == 0x2ffff0 and pb == 0x2ffff0, (hex(pa), hex(pb))
    assert b.cpu.reg_read(UC_ARM_REG_R4) == 0x44444444 and a.cpu.reg_read(UC_ARM_REG_R4) == 0x44444444
    assert b.cpu.reg_read(UC_ARM_REG_SP) == STACK and a.cpu.reg_read(UC_ARM_REG_SP) == STACK
    results['touch_blink_tailcall'] = {'v1': la, 'candidate': lb, 'returned_to_popped_lr': True,
                                       'r4_restored': True, 'sp_balanced': True}

    # 5. pairing blink 0x812d84..0x812d9e (fp = period): BLINK(0, ...) and BLINK(2, ...) in BOTH
    la, _ = a.run(0x812d84, 0x812d9e, regs=[(UC_ARM_REG_R11, 0x1f4)])
    lb, _ = b.run(0x812d84, 0x812d9e, regs=[(UC_ARM_REG_R11, 0x1f4)])
    assert la == [('BLINK', 0, 0x1f4, 0x1f4, 0xff), ('BLINK', 2, 0x1f4, 0x1f4, 0xff)], la
    assert lb == la, lb
    results['pairing_blink_preserved'] = {'v1': la, 'candidate': lb}

    # 6. the key-release path 0x81cbaa.. still turns LED2 OFF in both (harmless, but proves the
    #    release side is untouched: OFF(0) then continues); window ends at the next branch
    la, _ = a.run(0x81cbaa, 0x81cbb0)
    lb, _ = b.run(0x81cbaa, 0x81cbb0)
    assert la == lb == [('OFF', 0)], (la, lb)
    results['key_release_off_path_identical'] = True
    return results


def main():
    cand_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    cand = cand_path.read_bytes()
    report = {'candidate': str(cand_path), 'candidate_sha256': sha256(cand)}

    # base provenance
    v1 = load_main_v1()
    stock = STOCK.read_bytes()
    assert reproduce_main_v1(stock) == v1, 'main v1 from source != artifact'
    proof = flash_journal_proof()
    assert proof['image_validated_sha256_matches'], proof
    report['base'] = {'main_v1_sha256': MAIN_V1_SHA, 'from_source_equals_artifact': True, 'flash_journal': proof}

    # reproducibility + acceptance path (exactly what flash.py's gate computes)
    assert reproduce_candidate(stock) == cand, 'candidate != reproduce_candidate(stock)'
    built, manifest = build(v1)
    assert built == cand
    report['reproducible'] = True

    # image integrity + exact diff
    info = verify_image(cand)
    changed = {i for i, (x, y) in enumerate(zip(v1, cand)) if x != y}
    sites = set()
    for s in SITES:
        sites.update(range(s['va'] - MAIN_VA, s['va'] - MAIN_VA + 4))
    digest = set(range(SHA_OFFSET, SHA_OFFSET + 32))
    assert len(cand) == len(v1)
    assert changed <= sites | digest and sites <= changed, sorted(map(hex, changed ^ (sites | digest)))
    report['diff'] = {'changed_bytes': len(changed), 'site_bytes': len(sites & changed),
                      'digest_bytes': len(digest & changed), 'outside_sites_and_digest': 0}
    report['realtek_image'] = info

    # disassembly, before and after
    report['sites_before'] = check_sites(v1, patched=False)
    report['sites_after'] = check_sites(cand, patched=True)
    report['listing_after'] = {hex(s['va']): disasm_span(cand, s['va'] - 8, 16) for s in SITES}
    for va, chex in PRESERVED.items():
        off = va - MAIN_VA
        assert cand[off:off + len(chex) // 2].hex() == chex
    for name, (lo, hi) in NAMED_REGIONS.items():
        assert cand[lo - MAIN_VA:hi - MAIN_VA] == v1[lo - MAIN_VA:hi - MAIN_VA], name
    report['named_regions_identical'] = list(NAMED_REGIONS)

    # behaviour
    report['behavioural'] = behavioural(v1, cand)
    report['result'] = 'PASS'
    print(json.dumps(report, indent=2, default=str))


if __name__ == '__main__':
    main()
