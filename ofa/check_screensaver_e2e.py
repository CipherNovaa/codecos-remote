#!/usr/bin/env python3
"""End-to-end emulation of the SCREEN receive path with the exact bytes main sends.

Real code executed: Keil scatter-load (0x153c), UART init (0x64dc), rx/tx buffer setup
(0x1d3d8/0x1d3cc), the UART0 ISR per byte (0x65b8), the silence detector (0x63a8), the
frame validator + dispatcher (0x1c294 -> 0x1d444 -> 0x1d2bc -> table), the command
handler (stock 0x1cbd0 or our ss_cmd_stub/ss_command), the reply builder (0x1d386 ->
0x1d078/0x1d168/0x1d0d0) and the send path (0x1d40c -> 0x1d260 -> 0x6688).
Modelled: tick (0x5150), UART status/data register reads (0x645c/0x65b2/0x6414), the
flash controller (init/unlock/mode/erase/prog), delay 0xa4c4.
"""
import struct, sys
from unicorn import *
from unicorn.arm_const import *

import glob, os, json
DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'artifacts/screensaver-v15')
_man = json.load(open(os.path.join(DIR, 'manifest.json')))
IMG = os.path.join(DIR, _man['screen'].get('file', 'screen-screensaver-v6-DO-NOT-FLASH.bin')) if 'file' in _man['screen'] else sorted(glob.glob(os.path.join(DIR, 'screen-screensaver-*-DO-NOT-FLASH.bin')))[-1]
img = open(IMG, 'rb').read()

STAGES = {0x2d180: 'appended (dead flash!)', 0x1c294: 'dispatcher 0x1c294', 0x1d2bc: 'validator 0x1d2bc', 0x1c2f2: 'table jump 0x1c2f2',
          0x1c456: 'ERROR STUB 0x1c456', 0x1c46c: 'cmd02 stub', 0x1cbd0: 'cmd02 handler',
          0x1d386: 'F_REPLY 0x1d386', 0x1d078: 'append 0x1d078',
          0x1d0d0: 'build tx 0x1d0d0', 0x1d40c: 'dispatch_send 0x1d40c', 0x1d260: 'send 0x1d260',
          0x1d378: 'NAK 0x1d378', 0x1d392: 'ACK 0x1d392', 0x1d1a8: 'rx reset 0x1d1a8'}

class Harness:
    def __init__(self, syms):
        self.cpu = cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        cpu.mem_map(0, 0x40000); cpu.mem_write(0x1000, img)
        cpu.mem_write(0x2e000, b'\xff' * 0xc200)
        cpu.mem_map(0x20000000, 0x10000)
        cpu.mem_map(0x40000000, 0x40000)          # MMIO reads as 0, writes absorbed
        cpu.mem_map(0xe0000000, 0x10000)
        self.tick = 1000; self.rxbytes = []; self.tx = []; self.trace = []; self.flash = []
        self.retvals = {}
        for k, v in syms.items(): STAGES[int(v, 16)] = k
        cpu.hook_add(UC_HOOK_CODE, self.hook)
        cpu.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED, lambda u, t, ad, sz, v, d: print(f'  !! unmapped {ad:#x} at pc {u.reg_read(UC_ARM_REG_PC):#x}') or False)
        self.stop = 0x3f000

    def hook(self, cpu, a, size, _):
        if a in STAGES:
            self.trace.append((STAGES[a], cpu.reg_read(UC_ARM_REG_R0)))
        if a == 0x1d2bc + 0:  # capture the validator's return by hooking its pops
            pass
        r0, r1, r2 = (cpu.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        ret = None
        if a == 0x5150: ret = self.tick
        elif a == 0x645c:                          # uart flag read (base, flag): RX(0) pending iff bytes queued
            ret = 1 if (r1 == 0 and self.rxbytes) else 0
        elif a == 0x65b2: ret = self.rxbytes.pop(0) if self.rxbytes else 0
        elif a == 0x6414: ret = 0                  # clear flag
        elif a == 0x6688:                          # uart tx (buf, n)
            self.tx.append(bytes(cpu.mem_read(r0, r1))); ret = 0
        elif a in (0x4edc, 0x4fe4): ret = 0
        elif a == 0x1afbc:                         # stock unlock: ONE SLOCK bit (bank a[19:16] -> SLOCK0/1/2/3 at +0/4/0x10/0x14), modelled in MMIO
            self.flash.append(('unlock', r0)); bank = (r0 >> 16) & 0xf
            cpu.mem_write(0x40020030 + 4 * (bank if bank < 2 else bank + 2), struct.pack('<I', 1 << ((r0 & 0xffff) >> 11))); ret = 0
        elif a == 0x5034: self.flash.append(('erase', r0)); cpu.mem_write(r0, b'\xff' * 512); ret = 0
        elif a == 0x50c0:
            self.flash.append(('prog', r0, r2)); cpu.mem_write(r0, bytes(cpu.mem_read(r1, r2))); ret = 0
        elif a == 0x13e8 and self.in_flash_prepare: ret = 6
        elif a == 0xa4c4: ret = 0                  # delay
        if ret is not None:
            cpu.reg_write(UC_ARM_REG_R0, ret); cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))
    in_flash_prepare = True

    def call(self, va, *args, count=2_000_000):
        for i, v in enumerate(args): self.cpu.reg_write((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[i], v)
        self.cpu.reg_write(UC_ARM_REG_LR, self.stop | 1)
        self.cpu.emu_start(va | 1, self.stop, count=count)
        return self.cpu.reg_read(UC_ARM_REG_R0)

    def boot(self):
        cpu = self.cpu
        cpu.reg_write(UC_ARM_REG_SP, 0x20007e58)
        self.call(0x1560, 0x2cf08, 0x20000000, 0x710)   # Keil __decompress: RW init
        self.call(0x1f82c, 0x2d180, 0x20000710, 0x7748) # zero-init ZI
        # main()'s stack frame: rx buffer 521 B at sp+0x24, tx buffer 32 B at sp+4 (0x5bb8..0x5bd6)
        sp = 0x20007e58 - 0x230 - 0x100
        cpu.reg_write(UC_ARM_REG_SP, sp - 0x200)
        self.rxbuf, self.txbuf = sp + 0x24, sp + 4
        cpu.mem_write(0x20006c84, struct.pack('<IH', self.rxbuf, 0x209))   # 0x64dc: [buf, size]
        self.call(0x6424); self.call(0x6400)        # rx write pointer / count reset (as UART init does)
        self.call(0x1d3d8, self.rxbuf, 0x209)      # rx buffer
        self.call(0x1d3cc, self.txbuf, 0x20)       # tx buffer
        self.call(0x1d3fe)                         # boot-time (0x1a5c4 -> 0x5c44): tx enable
        # the main loop's first-run state (0x1d44c @ 0x1d46e): activity + 0x1f594 -- skip; keep RAM as booted

    def receive(self, frame):
        """Main-loop rearm (0x1d45e -> 0x1d36c), 8x00 wake preamble, 35 ms pause, the frame, 2 ms silence."""
        self.call(0x1d36c)
        self.trace.clear(); self.tx.clear()
        for b in b'\0' * 8 + frame:
            self.rxbytes = [b]
            self.call(0x65b8)
            if b == 0 and len(self.rxbytes) == 0:
                pass
        self.tick += 40
        n = struct.unpack('<H', self.cpu.mem_read(0x20006c8c + 4, 2))[0]
        return n

    def dispatch(self):
        n = self.call(0x6454)
        self.call(0x63a8)                          # silence wait (tick already advanced)
        self.call(0x1c294, 0x3445, n)
        return n


def frame(cmd, payload):
    body = bytes([0xa5, 0x5a, cmd, 0]) + struct.pack('<H', len(payload)) + payload
    return body + bytes([sum(body) & 0xff])

import json
man = json.load(open(os.path.join(DIR, 'manifest.json')))
syms = {k: v for k, v in man['screen']['symbols'].items()}

EXPECT = {                                             # (must-see stage, must-NOT-see stage, TX predicate)
    'stock VERSION cmd 02': ('cmd02 handler', 'ss_cmd_stub', lambda tx: any(t[2] == 0x02 for t in tx)),
    'BEGIN slot 0': ('ss_command', 'ERROR STUB 0x1c456', lambda tx: bool(tx)),
    'BEGIN slot 3 (erases 0x34000..0x35e00 only)': ('ss_command', 'NAK 0x1d378', lambda tx: bool(tx)),
    'BEGIN slot 6 (-> nak, no flash)': ('NAK 0x1d378', 'ERROR STUB 0x1c456', lambda tx: bool(tx)),
    'DATA @0 (4 bytes)': ('ss_command', 'ERROR STUB 0x1c456', lambda tx: bool(tx)),
    'SHOW (no image -> nak)': ('NAK 0x1d378', 'ERROR STUB 0x1c456', lambda tx: bool(tx)),
    'MODE sequential': ('ACK 0x1d392', 'NAK 0x1d378', lambda tx: bool(tx)),
    'CLEAR all (nothing present -> ack, no flash)': ('ACK 0x1d392', 'NAK 0x1d378', lambda tx: bool(tx)),
    'REG 16 via THRESHOLD alias (main v1 path)': ('ss_command', 'NAK 0x1d378',
                                                  lambda tx: any(t[2] == 0x02 and t[6:8] == bytes([0x90, 15]) for t in tx)),
}
FLASH = {                                              # (min erases, max erases, address window) expected per test
    'BEGIN slot 0': (16, 16, (0x2e000, 0x30000)),
    'BEGIN slot 3 (erases 0x34000..0x35e00 only)': (16, 16, (0x34000, 0x36000)),
    'BEGIN slot 6 (-> nak, no flash)': (0, 0, None),
    'CLEAR all (nothing present -> ack, no flash)': (0, 0, None),
    'MODE sequential': (0, 0, None),
}
failures = []
for name, f in (('stock VERSION cmd 02', frame(0x02, bytes([0, 0, 5, 7]))),
                ('BEGIN slot 0', frame(0x50, bytes([0x00, 0x00, 0x88, 0x1f]))),
                ('BEGIN slot 3 (erases 0x34000..0x35e00 only)', frame(0x50, bytes([0x00, 0x03, 0x88, 0x1f]))),
                ('BEGIN slot 6 (-> nak, no flash)', frame(0x50, bytes([0x00, 0x06, 0x88, 0x1f]))),
                ('DATA @0 (4 bytes)', frame(0x50, bytes([0x01, 0x00, 0x00, 1, 2, 3, 4]))),
                ('SHOW (no image -> nak)', frame(0x50, bytes([0x03]))),
                ('MODE sequential', frame(0x50, bytes([0x06, 0x01]))),
                ('CLEAR all (nothing present -> ack, no flash)', frame(0x50, bytes([0x05, 0xff]))),
                ('REG 16 via THRESHOLD alias (main v1 path)', frame(0x50, bytes([0x04, 0x10, 0xff])))):
    h = Harness(syms); h.boot()
    n = h.receive(f)
    print(f'=== {name}: wire {f.hex(" ")}')
    print(f'  rx count after ISRs = {n} (frame len {len(f)}); rxbuf = {bytes(h.cpu.mem_read(h.rxbuf, n)).hex(" ")}')
    h.dispatch()
    stages = [s for s, r in h.trace]
    print('  trace:', ' -> '.join(f'{s}' + (f'(r0={r:#x})' if s.startswith(('validator', 'table', 'dispatch', 'send')) else '') for s, r in h.trace))
    print('  TX bytes:', [t.hex(' ') for t in h.tx])
    print('  flash ops =', h.flash[:4], '... total', len(h.flash))
    must, must_not, txok = EXPECT[name]
    ok = must in stages and must_not not in stages and txok(h.tx) and n == len(f)      # the 8 wake-preamble zeros are not counted
    if name in FLASH:
        lo_n, hi_n, win = FLASH[name]
        erases = [a for k, a, *_ in h.flash if k == 'erase']
        ok = ok and lo_n <= len(erases) <= hi_n and all(win and win[0] <= a < win[1] for a in erases)
        ok = ok and all(win and win[0] <= a < win[1] for k, a, *_ in h.flash if k == 'prog')
    print('  ' + ('OK' if ok else 'FAIL'))
    if not ok:
        failures.append(name)
if failures:
    raise SystemExit(f'E2E FAIL: {failures}')
print('E2E PASS: with the main\'s exact wire bytes, cmd 0x50 reaches ss_command through the real ISR/validator/dispatcher and replies; '
      'cmd 0x02 still reaches the stock handler; BEGIN slot 3 erases only 0x34000..0x36000 and slot 6 naks; MODE/CLEAR ack; '
      'REG 16 masquerades as the version reply with 90 0f (ext_version 15).')
