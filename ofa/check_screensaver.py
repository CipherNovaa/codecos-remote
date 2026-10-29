#!/usr/bin/env python3
"""Behavioural validation of the screensaver candidates (offline Unicorn), v15.

v15 adds: the partial-counter lever proven against the REAL 0x1858 (counter 0x1e -> full
update 0x22/0xff and counter := 0; counter 5 -> partial and 6); every blit (sleep-draw,
SHOW) leaves the counter at 0x1e; the page-dispatch hook (pre-wait BUSY bounded, stock
dispatcher called with r4/r5 preserved, a pending restore forces the counter, sets
0x200008b8 and records the stock policy); the post-restore grace (sleep decisions that
would draw a NEW image return 0 for 20 s of awake time, the skip path still sleeps);
the restore arms rst_pending/grace_armed/rst_tick and records wake_snap; regs 84..99.

Executes the REAL in-image ARM code (all three regions) against modelled peripherals:
  * real dispatcher slice routes cmd 0x50 to ss_cmd_stub;
  * FRAMEBUFFER FORMAT PROOF with the real stock code: set_px_cb 0xbff0 is run over
    EVERY pixel of a random image and must produce exactly the blit's ~blob; the real
    RAM writer 0x19da must stream cmd 0x24 + those 8064 bytes; the real refresh 0x1858
    must issue a FULL update (0x22/0xff + 0x20) when the flag is forced and skip its
    own busy-wait as on the device (0x200008b8 set);
  * a55a cmd 0x50 BEGIN/DATA/COMMIT per slot (0, 3, 5: erase + program land inside THAT
    slot only), presence map, SHOW [slot] = awake blit, CLEAR slot/all, MODE, THRESHOLD
    on/off, REG masquerade (+alias) + STATUS; a modelled DEAD upper slot (erase fails)
    never becomes present and is never picked;
  * sleep hook: skip when the framebuffer already holds the CURRENT slot; otherwise
    pick (random: only filled slots, never the one on the panel when another is
    filled, every filled slot reached; sequential: filled slots in order), pre-wait,
    the stock flush wrapper around write+refresh in the stock order, post-refresh BUSY
    poll (cap / fallback / timeout), settle, [rearm] 0x1848, scrub, one-pass deferral
    when main talked, then the stock sleep in the same pass;
  * activity: restore (SHOWN := 0xff) only on genuine input while the saver is shown;
  * every flash op inside the addressed slot's 8 KiB window.
  * main (optional v2): W 0C replies, forwarding/refusal, reply relay.
Peripherals are modelled; no boot, display, radio or real flash is executed.
See check_screensaver_e2e.py for the full receive-path emulation with real wire bytes.
"""
import argparse
import hashlib
import json
import random
import struct
import zlib
from pathlib import Path

from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE)
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                               UC_ARM_REG_R4, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)

from check_touch_pages import FirmwareConfig

SCREEN_VA = 0x1000
SLOT = 0x2e000                  # slot 0
SLOT_SIZE, NSLOTS = 0x2000, 6
SLOT_END = SLOT + SLOT_SIZE
SLOTS_END = SLOT + NSLOTS * SLOT_SIZE
IMG_BYTES = 8072
FB, FB_BYTES = 0x20004c3c, 8064
W, H, STRIDE = 168, 384, 21
RAM_MAGIC = 0x46525353          # 'SSRF' (v15 layout)
PAYLOAD = 0x2000C000
STOP = 0x3F000
ST_ADDR = 0x20007f80
ST_SIZE = 84
NO_SLOT = 0xff
PARTIAL_CNT, SYNC_SKIP = 0x20000a24, 0x200008b8      # v15: the stock partial-update counter / skip-the-stock-wait flag
SLEPT_FLAG, LPTIM_FLAG = 0x20000872, 0x20000873
GRACE_MS, FULL_AT = 20000, 0x1e


def slot_base(i):
    return SLOT + i * SLOT_SIZE
TARGET, SHOWN = 0x200003ec, 0x200003f4
LP_MODE, BL_DONE = 0x20006bcc, 0x20000871
ARMED, PA2_EVENT, FULL_FLAG, RX_COUNT = 0x20006e44, 0x20006e43, 0x20000a25, 0x20006c90
WAKE_TOUCH, WAKE_RXEDGE, RX_EDGE, PANEL_ABSENT = 0x20006e50, 0x20006e51, 0x2000088c, 0x20006bc4
BATT_PCT, BATT_MV, BATT_RAW, SETTINGS_253, REPLY_CMD = 0x200003d4, 0x20000a74, 0x20000a70, 0x20000253, 0x20006c7a
SLEEP_STOCK = 0xbe8c

F = {0x1d204: 'payload', 0x1d218: 'len', 0x1d392: 'ack', 0x1d378: 'nak', 0x1d386: 'reply',
     0x13e8: 'clkdiv', 0x4edc: 'fl_init', 0x1afbc: 'unlock', 0x4fe4: 'fl_mode',
     0x5034: 'erase', 0x50c0: 'prog',
     0x6598: 'flush_begin', 0xa1fe: 'pb0', 0x17a8: 'touch_mask', 0x19da: 'epd_write', 0x1858: 'epd_refresh',
     0x5cfc: 'flush_end', 0x17fc: 'nvic', 0x1848: 'touch_arm',
     0xea10: 'busy', 0x5150: 'tick', 0xa4c4: 'delay',
     SLEEP_STOCK: 'sleep', 0xbeec: 'lptim', 0x1ec5c: 'dispatch'}
FIELDS = {'rx_active': 4, 'draw_target': 5, 'last_err': 6, 'inits': 7, 'cmd_calls': 8, 'sleep_hits': 9,
          'sleep_draws': 10, 'show_blits': 11, 'act_calls': 12, 'restores': 13, 'act_mode': 14, 'deferred': 15,
          'act_caller': 16, 'restore_caller': 17, 'ignored_caller': 18, 'ignored': 19,
          'skips': 20, 'ign_src': 21, 'rst_src': 22, 'busy_pre': 23, 'busy_ms10': 24, 'busy_timeouts': 25,
          'saver_active': 26, 'lptim_since_draw': 27, 'suppressed': 28, 'lptim_wakes': 29, 'scrubbed': 30,
          'busy_first': 31, 'pre_ms10': 32, 'busy_fallbacks': 33, 'pa2_scrubbed': 34, 'rearms': 35,
          'fb_lo': 36, 'fb_hi': 37, 'last_path': 38, 'defers': 39,
          'cur_slot': 40, 'mode': 41, 'thr_lo': 42, 'thr_hi': 43, 'seed': 44,      # seed is a u32 at 44..47
          'picks': 48, 'map_at_draw': 49, 'disabled': 50, 'rx_slot': 51,
          'rx_crc': 52, 'rx_frames': 56, 'rx_bytes': 58, 'err_addr': 60,       # v14: u32 / u16 / u16 / u32
          'err_code': 64, 'diag': 65, 'slock_tries': 66, 'pad': 67,
          'rst_tick': 68, 'rst_pending': 72, 'grace_armed': 73, 'disp_calls': 74, 'disp_forced': 75,   # v15
          'disp_busy_pre': 76, 'disp_ms10': 77, 'disp_timeouts': 78, 'grace_defers': 79,
          'quiet_ms10': 80, 'wake_snap': 81, 'rst_policy': 82, 'rst_sync': 83}
U32_FIELDS, U16_FIELDS = ('seed', 'rx_crc', 'err_addr', 'rst_tick'), ('rx_frames', 'rx_bytes')
SLOCK_REG = {0: 0x40020030, 1: 0x40020034, 2: 0x40020040, 3: 0x40020044}
CALLER_LR = {1: 0x1d473, 2: 0x1d48f, 3: 0x1d531, 4: 0x1d635, 5: 0x12345}
WRAP = ['flush_begin', 'pb0', 'touch_mask', 'epd_write', 'epd_refresh', 'pb0', 'flush_end', 'nvic']


def wrap_ok(s, i):
    """events[i:i+8] is the stock flush wrapper in the stock order with PB0 := 1 ... := 0."""
    return s.kinds()[i:i + 8] == WRAP and s.events[i + 1] == ('pb0', 1) and s.events[i + 5] == ('pb0', 0)


def random_pixels(seed=12345):
    rnd = random.Random(seed)
    return bytes(rnd.getrandbits(8) for _ in range(FB_BYTES))


def blob_of(pixels):
    return b'\xff\xff\xff\xff\x00\x00\x00\xff' + pixels


def inverted(pixels):
    return bytes((~b) & 0xff for b in pixels)


class Screen:
    def __init__(self, image):
        self.cpu = cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        cpu.mem_map(0, 0x40000)
        cpu.mem_write(SCREEN_VA, image)
        cpu.mem_write(SLOT, b'\xff' * (SLOTS_END - SLOT))
        cpu.mem_map(0x20000000, 0x10000)
        cpu.mem_map(0x40000000, 0x100000)             # MMIO: FLASH_SLOCK0..3 modelled below, everything else reads 0
        self.busy_polls, self.refresh_busy, self.tick = 0, 40, 1000
        self.disp_full, self.disp_sync = 1, 1         # model: what the stock dispatcher leaves in FULL_FLAG / 0x200008b8 for the page
        self.dead_from = None                         # model: flash at/after this address refuses erase/program (returns 9)
        self.slock_dead = False                       # model: the SLOCK bit never sets (v14 fl_unlock must fail with 0xa)
        self.erase_void_from = None                   # model: erase 'succeeds' but leaves the data (RM 10.6.1 protected sector)
        self.prog_fail_at = None                      # model: the programmer's readback mismatches at this address (returns 1)
        self.reset_log()
        cpu.hook_add(UC_HOOK_CODE, self._hook)

    def reset_log(self):
        self.events, self.flash_ops, self.payload, self.reply = [], [], b'', None

    def _hook(self, cpu, address, size, _):
        name = F.get(address)
        if name is None:
            return
        r0, r1, r2 = (cpu.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        ret = 0
        if name == 'payload':
            ret = PAYLOAD
        elif name == 'len':
            ret = len(self.payload)
        elif name in ('ack', 'nak', 'sleep', 'lptim', 'flush_begin', 'touch_mask', 'flush_end', 'touch_arm'):
            self.events.append(name)
        elif name == 'reply':
            self.reply = bytes(cpu.mem_read(r0, r1)); self.events.append('ack')
        elif name == 'clkdiv':
            ret = 6
        elif name in ('fl_init', 'fl_mode'):
            ret = 0
        elif name == 'unlock':                        # the stock writes ONE bit into the bank's SLOCK register (never ORs)
            self.flash_ops.append(('unlock', r0))
            bank = (r0 >> 16) & 0xf
            cpu.mem_write(SLOCK_REG[bank], struct.pack('<I', 0 if self.slock_dead else 1 << ((r0 & 0xffff) >> 11)))
        elif name == 'erase':
            self.flash_ops.append(('erase', r0))
            if self.dead_from is not None and r0 >= self.dead_from:
                ret = 9
            elif self.erase_void_from is not None and r0 >= self.erase_void_from:
                pass                                  # returns 0, data untouched
            else:
                cpu.mem_write(r0, b'\xff' * 512)
        elif name == 'prog':
            self.flash_ops.append(('prog', r0, r2))
            if self.dead_from is not None and r0 >= self.dead_from:
                ret = 9
            elif self.prog_fail_at is not None and r0 <= self.prog_fail_at < r0 + r2:
                ret = 1
            else:
                cpu.mem_write(r0, bytes(cpu.mem_read(r1, r2)))
        elif name == 'pb0':
            assert r0 == 1, 'PB0 wrapper must be called as 0xa1fe(1, v)'
            self.events.append(('pb0', r1))
        elif name == 'nvic':
            self.events.append(('nvic', r0, r1, r2))
        elif name == 'epd_write':
            self.events.append(('epd_write', r0))
        elif name == 'epd_refresh':                   # the real 0x1858's mode choice (proven in verify_format): flag, else the counter
            cnt = self.rd8(PARTIAL_CNT)
            full = self.rd8(FULL_FLAG) == 1 or cnt >= FULL_AT
            self.wr8(PARTIAL_CNT, 0 if full else cnt + 1)
            self.events.append(('epd_refresh', 1 if full else 0))
            self.busy_polls = self.refresh_busy          # the panel goes BUSY for the refresh
        elif name == 'dispatch':                      # the stock page dispatcher: builds the page, sets 0x200008b8 and FULL_FLAG per its policy
            self.events.append(('dispatch', r0))
            self.wr8(SYNC_SKIP, self.disp_sync); self.wr8(FULL_FLAG, self.disp_full)
        elif name == 'busy':
            ret = 1 if self.busy_polls > 0 else 0
            self.busy_polls -= 1
            self.events.append('busy')
        elif name == 'tick':
            ret = self.tick
        elif name == 'delay':
            self.tick += r0; self.events.append(('delay', r0))
        cpu.reg_write(UC_ARM_REG_R0, ret)
        cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))

    def call(self, va, r0=0, payload=None):
        self.reset_log()
        if payload is not None:
            self.payload = payload
            self.cpu.mem_write(PAYLOAD, payload)
        self.cpu.reg_write(UC_ARM_REG_R0, r0)
        self.cpu.reg_write(UC_ARM_REG_SP, 0x20007000)
        self.cpu.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.cpu.emu_start(va | 1, STOP, count=8000000)
        return self.cpu.reg_read(UC_ARM_REG_R0)

    def seed_state(self, **kw):
        st = bytearray(ST_SIZE); struct.pack_into('<I', st, 0, RAM_MAGIC)
        st[FIELDS['cur_slot']] = NO_SLOT                # as ss_init leaves it
        for k, v in kw.items():
            if k in U32_FIELDS:
                struct.pack_into('<I', st, FIELDS[k], v)
            elif k in U16_FIELDS:
                struct.pack_into('<H', st, FIELDS[k], v)
            else:
                st[FIELDS[k]] = v
        self.cpu.mem_write(ST_ADDR, bytes(st))

    def field(self, name):
        return self.cpu.mem_read(ST_ADDR + FIELDS[name], 1)[0]

    def rd(self, va):
        return struct.unpack('<I', self.cpu.mem_read(va, 4))[0]

    def rd8(self, va):
        return self.cpu.mem_read(va, 1)[0]

    def wr(self, va, val):
        self.cpu.mem_write(va, struct.pack('<I', val))

    def wr8(self, va, val):
        self.cpu.mem_write(va, bytes([val]))

    def put_slot(self, pixels=None, slot=0):
        pixels = pixels if pixels is not None else bytes(FB_BYTES)
        self.cpu.mem_write(slot_base(slot), b'SSAV' + struct.pack('<HH', IMG_BYTES, 0) + blob_of(pixels))

    def slot_present(self, i):
        return bytes(self.cpu.mem_read(slot_base(i), 6)) == b'SSAV' + struct.pack('<H', IMG_BYTES)

    def slot_map(self):
        return sum(1 << i for i in range(NSLOTS) if self.slot_present(i))

    def fb(self):
        return bytes(self.cpu.mem_read(FB, FB_BYTES))

    def kinds(self):
        return [e[0] if isinstance(e, tuple) else e for e in self.events]


def check_flash_window(ops, slot=0):
    lo, hi = slot_base(slot), slot_base(slot) + SLOT_SIZE
    for op in ops:
        assert lo <= op[1] < hi, f'flash op {op} escapes slot {slot}'
        if op[0] == 'prog':
            assert op[1] + op[2] <= hi, f'program {op} overruns slot {slot}'


class Stock:
    """Bare emulator for the REAL stock EPD routines (no per-instruction hook except the SPI/wait primitives)."""
    def __init__(self, image):
        self.cpu = cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        cpu.mem_map(0, 0x40000); cpu.mem_write(SCREEN_VA, image)
        cpu.mem_map(0x20000000, 0x10000)
        self.stream, self.waits = [], 0

    def spi_hooks(self):
        def h(cpu, a, size, _):
            r0 = cpu.reg_read(UC_ARM_REG_R0)
            if a == 0x1e1b8:
                self.stream.append(('cmd', r0))
            elif a == 0x1e1d6:
                self.stream.append(('data', r0))
            elif a == 0x1e160:
                self.waits += 1
            elif a == 0x1e138:                      # SPI read (temperature): 1 byte into [r0]
                cpu.mem_write(r0, b'\x19')
            elif a == 0x6720:                       # driver helper before the full update (hardware); skipped
                pass
            else:
                return
            cpu.reg_write(UC_ARM_REG_R0, 0)
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))
        self.cpu.hook_add(UC_HOOK_CODE, h, begin=0x1e138, end=0x1e1d8)
        self.cpu.hook_add(UC_HOOK_CODE, h, begin=0x6720, end=0x6722)

    def call(self, va, *regs, stack=b'', count=100000):
        cpu = self.cpu
        for i, v in enumerate(regs):
            cpu.reg_write((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[i], v)
        sp = 0x20007000 - len(stack)
        if stack:
            cpu.mem_write(sp, stack)
        cpu.reg_write(UC_ARM_REG_SP, sp)
        cpu.reg_write(UC_ARM_REG_LR, STOP | 1)
        cpu.emu_start(va | 1, STOP, count=count)
        return cpu.reg_read(UC_ARM_REG_R0)


def verify_format(image):
    """The framebuffer format finding, measured against the stock code, not assumed."""
    pixels = random_pixels()
    want = inverted(pixels)
    # 1. set_px_cb 0xbff0 over EVERY pixel: white (0xff) where the blob bit is 0, black (0x00) where it is 1.
    s = Stock(image)
    s.cpu.mem_write(FB, b'\xaa' * FB_BYTES)              # both the OR path and the clear path must be exercised
    for y in range(H):
        row = pixels[y * STRIDE:(y + 1) * STRIDE]
        for x in range(W):
            black = (row[x >> 3] >> (7 - (x & 7))) & 1
            color = 0x00 if black else 0xff
            s.call(0xbff0, 0, 0, W, x, stack=struct.pack('<III', y, color, 0xff), count=400)
    got = bytes(s.cpu.mem_read(FB, FB_BYTES))
    assert got == want, 'the stock set_px_cb does not paint ~blob (bit order / stride / polarity mismatch)'
    # polarity spot check on the real code: a lone white pixel sets its bit, a lone black pixel clears it
    s.cpu.mem_write(FB, bytes(FB_BYTES)); s.call(0xbff0, 0, 0, W, 5, stack=struct.pack('<III', 7, 0xff, 0xff), count=400)
    assert s.cpu.mem_read(FB + 7 * STRIDE, 1)[0] == 1 << 2, 'white pixel (5,7) must SET bit 2 of byte 0 of row 7'
    s.cpu.mem_write(FB, b'\xff' * FB_BYTES); s.call(0xbff0, 0, 0, W, 167, stack=struct.pack('<III', 383, 0x00, 0xff), count=400)
    assert s.cpu.mem_read(FB + 383 * STRIDE + 20, 1)[0] == 0xfe, 'black pixel (167,383) must CLEAR bit 0 of the last byte'
    # 2. the real RAM writer streams cmd 0x24 + the 8064 framebuffer bytes, unchanged
    s = Stock(image); s.spi_hooks()
    s.cpu.mem_write(FB, want)
    s.call(0x19da, FB, count=2000000)
    assert s.stream[0] == ('cmd', 0x24) and len(s.stream) == 1 + FB_BYTES, (s.stream[:3], len(s.stream))
    assert bytes(v for k, v in s.stream[1:]) == want, '0x19da must send the framebuffer verbatim'
    assert s.waits == 2, '0x19da brackets the write with the stock wait'
    # 3. the real refresh does a FULL update when the flag is forced and skips its final wait as on the device
    for flag, expect in ((1, 0xff), (0, 0xcf)):
        s = Stock(image); s.spi_hooks()
        s.cpu.mem_write(FULL_FLAG, bytes([flag])); s.cpu.mem_write(0x200008b8, b'\x01'); s.cpu.mem_write(0x20000a24, b'\x00')
        s.cpu.mem_write(0x20000a22, b'\x19'); s.cpu.mem_write(0x20000a7a, b'\x00')
        s.call(0x1858, count=200000)
        i = len(s.stream) - 1 - s.stream[::-1].index(('cmd', 0x22))     # the LAST display-update-control (0xc030 also sends 0x22/0xa1 to load the LUT)
        assert s.stream[i + 1] == ('data', expect) and s.stream[i + 2] == ('cmd', 0x20), (flag, s.stream)
        if flag:
            assert s.stream[:3] == [('cmd', 0x18), ('data', 0x80), ('cmd', 0x22)] and ('cmd', 0x1a) in s.stream, 'full update = temperature + LUT load + 0x22/0xff + 0x20'
        assert s.waits == (1 if flag else 0), 'only 0x17c4\'s own wait inside the full update; no final wait (0x200008b8 set)'
    # 4. v15 lever, against the REAL 0x1858: with the flag CLEAR, counter 0x1e -> a FULL update and counter := 0; counter 5 -> partial, 6.
    #    And with 0x200008b8 clear (state-0 page group) the tail runs the stock wait 0x1e160 -- the RST-on-timeout wait v15 bypasses.
    for cnt, expect, after, sync, waits in ((FULL_AT, 0xff, 0, 1, 1), (5, 0xcf, 6, 1, 0), (FULL_AT, 0xff, 0, 0, 2), (0xff, 0xff, 0, 1, 1)):
        s = Stock(image); s.spi_hooks()
        s.cpu.mem_write(FULL_FLAG, b'\x00'); s.cpu.mem_write(SYNC_SKIP, bytes([sync])); s.cpu.mem_write(PARTIAL_CNT, bytes([cnt]))
        s.cpu.mem_write(0x20000a22, b'\x19'); s.cpu.mem_write(0x20000a7a, b'\x00'); s.cpu.mem_write(0x200003b5, b'\x00')
        s.call(0x1858, count=200000)
        i = len(s.stream) - 1 - s.stream[::-1].index(('cmd', 0x22))
        assert s.stream[i + 1] == ('data', expect) and s.stream[i + 2] == ('cmd', 0x20), (cnt, s.stream)
        assert s.cpu.mem_read(PARTIAL_CNT, 1)[0] == after, 'the real 0x1858 resets the counter after a full update / increments it after a partial'
        assert s.waits == waits, (cnt, sync, s.waits)
    return pixels


def verify_screen(image, syms, variant):
    ss = {k: int(v, 16) for k, v in syms.items()}
    rearm = variant == 'rearm'
    checks = 0

    # --- the REAL dispatcher slice (0x1c2f2..0x1c302, r0 = cmd) lands cmd 0x50 on ss_cmd_stub ---
    for cmd, want in ((0x50, ss['ss_cmd_stub'] & ~1), (0x51, 0x1c456), (0x02, 0x1c46c), (0x40, 0x1c4e4)):
        s = Screen(image); landed = []
        h = s.cpu.hook_add(UC_HOOK_CODE, lambda cpu, a, sz, _, L=landed: (a not in range(0x1c2f2, 0x1c304)) and (L.append(a), cpu.emu_stop()))
        s.cpu.reg_write(UC_ARM_REG_R0, cmd); s.cpu.reg_write(UC_ARM_REG_SP, 0x20007000)
        s.cpu.emu_start(0x1c2f2 | 1, STOP, count=50); s.cpu.hook_del(h)
        assert landed and landed[0] == want, (hex(cmd), [hex(x) for x in landed], hex(want))
    assert struct.unpack_from('<I', image, 0x1edf8 - SCREEN_VA)[0] == 0x1ee65, 'page table[0x59] must stay stock in v12'
    checks += 1

    # --- framebuffer format, measured against the stock code ---
    pixels = verify_format(image)
    want = inverted(pixels)
    checks += 1

    # --- the blit (ss_push via SHOW): copy/invert + the stock flush wrapper in the stock order, full refresh ---
    for pre_flag in (0, 1):
        s = Screen(image); s.seed_state(); s.put_slot(pixels); s.wr8(FULL_FLAG, pre_flag)
        s.cpu.mem_write(FB, b'\x55' * FB_BYTES)
        s.wr8(PARTIAL_CNT, 7)
        s.call(ss['ss_command'], payload=bytes([0x03]))
        assert s.events[:1] == ['busy'] and wrap_ok(s, 1) and s.events[8] == ('nvic', 6, 3, 1) and s.events[9] == 'ack', s.events
        assert len(s.events) == 10, 'SHOW = pre-wait + wrapper + write + refresh + ack, no post-refresh waiting'
        assert s.fb() == want, 'framebuffer must hold the INVERTED blob'
        assert ('epd_write', FB) in s.events and ('epd_refresh', 1) in s.events, 'write from 0x20004c3c; refresh with the full flag forced'
        assert s.rd8(FULL_FLAG) == pre_flag, 'full-refresh flag restored'
        assert s.rd8(PARTIAL_CNT) == FULL_AT, 'v15: after the blit the counter is armed so the NEXT stock flush is a full update'
        assert s.field('show_blits') == 1 and s.field('last_path') == 2 and s.field('saver_active') == 0
        assert (s.field('fb_lo'), s.field('fb_hi')) == (0x80, 0x1f), '8064 bytes written'
        assert s.field('cur_slot') == 0, 'SHOW with no current slot picked the only filled slot'
        assert not s.flash_ops
    # chain: our framebuffer through the REAL 0x19da gives cmd 0x24 + ~blob
    st = Stock(image); st.spi_hooks(); st.cpu.mem_write(FB, s.fb()); st.call(0x19da, FB, count=2000000)
    assert bytes(v for k, v in st.stream[1:]) == want
    # SHOW without an image -> nak, nothing written
    s = Screen(image); s.seed_state(); s.cpu.mem_write(FB, b'\x55' * FB_BYTES)
    s.call(ss['ss_command'], payload=bytes([0x03]))
    assert s.events == ['nak'] and s.fb() == b'\x55' * FB_BYTES
    checks += 1

    # --- BEGIN / DATA / COMMIT ---
    s = Screen(image); s.cpu.mem_write(ST_ADDR, bytes(ST_SIZE))
    s.call(ss['ss_command'], payload=bytes([0x00, 0]) + struct.pack('<H', IMG_BYTES))
    assert s.events == ['ack'] and s.field('rx_active') == 1 and s.field('cmd_calls') == 1
    erases = [o for o in s.flash_ops if o[0] == 'erase']
    assert len(erases) == 16 and erases[0][1] == SLOT and erases[-1][1] == SLOT_END - 512
    check_flash_window(s.flash_ops)
    chunk = bytes(range(200))
    s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', 0) + chunk)
    assert s.events == ['ack'] and bytes(s.cpu.mem_read(SLOT + 8, 200)) == chunk
    check_flash_window(s.flash_ops)
    s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', IMG_BYTES - 72) + chunk[:72])
    assert s.events == ['ack']
    body = bytes((i * 7) & 0xff for i in range(IMG_BYTES))
    s.cpu.mem_write(SLOT + 8, body)
    s.call(ss['ss_command'], payload=bytes([0x02]) + struct.pack('<I', zlib.crc32(body) & 0xffffffff))
    assert s.events == ['ack'] and bytes(s.cpu.mem_read(SLOT, 6)) == b'SSAV' + struct.pack('<H', IMG_BYTES)
    assert s.field('rx_active') == 0
    check_flash_window(s.flash_ops)
    checks += 1

    # bad crc / overrun / no BEGIN / slot 6 / unknown / bad REG / absent SHOW slot / MODE 2 / CLEAR 6 -> nak, no flash
    s = Screen(image); s.seed_state(rx_active=1)
    s.call(ss['ss_command'], payload=bytes([0x02]) + struct.pack('<I', 0xdeadbeef))
    assert s.events == ['nak'] and bytes(s.cpu.mem_read(SLOT, 4)) != b'SSAV'
    for bad, st_ in ((bytes([0x01]) + struct.pack('<H', IMG_BYTES - 10) + bytes(20), 1),
                     (bytes([0x01]) + struct.pack('<H', 0) + bytes(4), 0),
                     (bytes([0x00, 6]) + struct.pack('<H', IMG_BYTES), 0),
                     (bytes([0x00, 0]) + struct.pack('<H', 1), 0),
                     (bytes([0x08, 100]), 0), (bytes([0x08]), 0), (bytes([0x04, 0x05, 0xfe]), 0), (bytes([0x04, 100, 0xff]), 0),
                     (bytes([0x07, 1]), 0), (bytes([0x09]), 0), (b'', 0), (bytes([0x03, 1]), 0), (bytes([0x03, 6]), 0),
                     (bytes([0x03, 0, 0]), 0), (bytes([0x05, 6]), 0), (bytes([0x05]), 0), (bytes([0x06, 2]), 0),
                     (bytes([0x06]), 0), (bytes([0x04, 1]), 0)):
        s = Screen(image); s.seed_state(rx_active=st_)
        s.call(ss['ss_command'], payload=bad)
        assert s.events == ['nak'], (bad.hex(), s.events)
        assert not [o for o in s.flash_ops if o[0] in ('erase', 'prog')], bad.hex()
    checks += 1

    # --- readback: REG masquerade (native + THRESHOLD alias) and STATUS ---
    s = Screen(image); s.seed_state(cmd_calls=4, sleep_hits=9, sleep_draws=3, show_blits=5, restores=2, act_calls=7, act_mode=1,
                                    inits=1, draw_target=0x5b, skips=6, busy_ms10=17, busy_pre=1, pre_ms10=2, busy_fallbacks=1,
                                    pa2_scrubbed=3, rearms=4, fb_lo=0x80, fb_hi=0x1f, last_path=1, defers=2,
                                    cur_slot=0, mode=1, thr_lo=0x2c, thr_hi=1, seed=0x11223344, picks=7, map_at_draw=0x21,
                                    disabled=0, rx_slot=5,
                                    rx_crc=0x11223344, rx_frames=0x0102, rx_bytes=0x0304, err_addr=0x0002e808, err_code=7, diag=0x13, slock_tries=1,
                                    rst_tick=0x55667788, rst_pending=1, grace_armed=1, disp_calls=0x21, disp_forced=0x22, disp_busy_pre=1,
                                    disp_ms10=0x23, disp_timeouts=0x24, grace_defers=0x25, quiet_ms10=0x26, wake_snap=0x27, rst_policy=1, rst_sync=0)
    s.cpu.mem_write(slot_base(5) + 8, b'\x5a')                    # reg 82 = first DATA byte of the slot being written
    s.cpu.mem_write(0x40020024, b'\x02\x00\x00\x00'); s.cpu.mem_write(0x40020040, b'\x00\x00\x00\xf0')
    s.cpu.mem_write(0x40020044, b'\x0f\xa5\x5c\x00'); s.cpu.mem_write(0x40020020, b'\x01\x02\x00\x00')
    s.put_slot(pixels); s.put_slot(random_pixels(7), 4); s.cpu.mem_write(FB, want)
    s.wr8(BATT_PCT, 75); s.wr(BATT_MV, 5500); s.wr(BATT_RAW, 3413); s.wr8(SETTINGS_253, 0x81)
    s.wr(SHOWN, 0x34); s.wr(TARGET, 0x5b); s.wr8(LP_MODE, 1); s.wr8(BL_DONE, 1); s.wr8(ARMED, 1); s.wr8(PANEL_ABSENT, 1); s.wr8(PA2_EVENT, 1)
    s.wr8(PARTIAL_CNT, 0x11); s.wr8(FULL_FLAG, 0); s.wr8(SYNC_SKIP, 1); s.wr8(0x20000254, 30)
    tail = bytes([75, 5500 & 0xff, 5500 >> 8, 3413 & 0xff, 3413 >> 8, 0x81])
    expect = {0: 0b10001, 1: 5, 2: 9, 3: 3, 4: 5, 5: 2, 6: 7, 7: 1, 8: 0x34, 9: 0x5b, 10: 1, 13: 1, 14: 0x5b,
              15: 0x56, 16: 15, 17: 75, 18: 1, 19: 0, 20: 1, 21: 5500 & 0xff, 22: 5500 >> 8, 23: 3 if rearm else 4,
              28: 1, 31: 6, 32: 17, 39: 30, 41: 1, 42: 2, 43: 1, 44: 3, 45: 4, 46: 0x80, 47: 0x1f, 48: 1, 49: 1, 50: 1, 51: 2,
              52: 0, 53: 1, 54: 0x2c, 55: 1, 56: 7, 57: 0x21, 58: 0, 59: 5, 60: 0x44,
              61: 0x44, 62: 0x33, 63: 0x22, 64: 0x11, 65: 0x02, 66: 0x01, 67: 0x04, 68: 0x03, 69: 0x08, 70: 0xe8, 71: 0x02, 72: 0x00,
              73: 7, 74: 0x13, 75: 1, 76: 0, 77: 0x02, 78: 0x01, 79: 0xf0, 80: 0x0f, 81: 0xa5, 82: 0x5c, 83: 0x5a,
              84: 1, 85: 1, 86: 0x21, 87: 0x22, 88: 1, 89: 0x23, 90: 0x24, 91: 0x25, 92: 0x26, 93: 0x27, 94: 1, 95: 0,   # v15
              96: 0x11, 97: 0, 98: 1, 99: GRACE_MS // 1000}
    n = 4
    for idx, val in expect.items():
        n += 1; expect[1] = n                               # cmd_calls counts this very command
        s.call(ss['ss_command'], payload=bytes([0x08, idx]))
        want_v = val if idx != 1 else n
        assert s.reply == bytes([0x80 | idx, want_v]) + tail and s.rd8(REPLY_CMD) == 2, (idx, s.reply, want_v)
        s.wr8(REPLY_CMD, 0)
        n += 1
        s.call(ss['ss_command'], payload=bytes([0x04, idx, 0xff]))   # alias for the installed main v1
        want_v = val if idx != 1 else n
        assert s.reply == bytes([0x80 | idx, want_v]) + tail and s.rd8(REPLY_CMD) == 2 and not s.flash_ops, (idx, s.reply)
        s.wr8(REPLY_CMD, 0)
    s.cpu.mem_write(FB, bytes(FB_BYTES)); s.call(ss['ss_command'], payload=bytes([0x08, 28]))
    assert s.reply[:2] == bytes([0x80 | 28, 0]), 'fb_match is live'
    s.cpu.mem_write(FB, want); s.cpu.mem_write(ST_ADDR + FIELDS['cur_slot'], b'\x04'); s.call(ss['ss_command'], payload=bytes([0x08, 28]))
    assert s.reply[:2] == bytes([0x80 | 28, 0]), 'fb_match compares against the CURRENT slot'
    s.cpu.mem_write(ST_ADDR + FIELDS['cur_slot'], b'\xff'); s.call(ss['ss_command'], payload=bytes([0x08, 28]))
    assert s.reply[:2] == bytes([0x80 | 28, 0]), 'no current slot: fb_match 0'
    s.call(ss['ss_command'], payload=bytes([0x04, 0xff, 0xff]))
    assert s.reply == bytes([0x01, 0x3f]) + tail and s.rd8(REPLY_CMD) == 2
    for idx in range(100):
        s.call(ss['ss_command'], payload=bytes([0x08, idx]))
        assert s.reply[0] not in (0x00, 0xff)
    s.call(ss['ss_command'], payload=bytes([0x08, 100])); assert s.events == ['nak'], 'reg 100 is past NREGS'
    s.wr8(REPLY_CMD, 0)
    s.call(ss['ss_command'], payload=bytes([0x07]))
    assert s.reply and s.reply[:2] == b'S7' and len(s.reply) == 16 and s.reply[2] == 0b10001 and s.reply[10] == 0x34 and s.reply[11] == 0x5b
    assert s.rd8(REPLY_CMD) == 0, 'STATUS is a plain reply, not a masquerade'
    checks += 1

    # --- sleep hook: the blit in the stock order, then the waits, then the stock sleep in the same pass ---
    def fresh(**kw):
        s = Screen(image); s.seed_state(**kw); s.put_slot(pixels)
        s.wr(SHOWN, 0x34); s.wr(TARGET, 0x34); s.wr8(BL_DONE, 1); s.wr8(ARMED, 1); s.wr8(FULL_FLAG, 0)
        s.wr(0x20006bc8, 1000); s.cpu.mem_write(FB, b'\x55' * FB_BYTES); s.cpu.mem_write(RX_COUNT, b'\x00\x00')
        return s
    s = fresh(); s.refresh_busy = 40                       # panel busy for 40 polls (~200 ms) after the refresh command
    assert s.call(ss['ss_sleep_hook']) is not None
    k = s.kinds()
    assert k[0] == 'busy' and wrap_ok(s, 1) and s.events[8] == ('nvic', 6, 3, 1) and k[-1] == 'sleep', s.events
    assert s.fb() == want and ('epd_write', FB) in s.events and ('epd_refresh', 1) in s.events and s.rd8(FULL_FLAG) == 0
    assert k.count('busy') == 1 + 41, 'one pre-read (idle) + 41 post-refresh polls (40 busy + 1 idle)'
    delays = [e[1] for e in s.events if isinstance(e, tuple) and e[0] == 'delay']
    assert delays[0] == 5 and delays.count(5) == 41 and delays[-2:] == [100, 50], delays
    i_arm = k.index('touch_arm') if rearm else None
    if rearm:
        assert k.count('touch_arm') == 1 and k[i_arm - 1] == 'delay' and s.events[i_arm - 1] == ('delay', 100) and s.events[i_arm + 1] == ('delay', 50)
        assert i_arm > len(k) - 1 - k[::-1].index('busy'), 're-arm only after the panel is idle'
        assert s.field('rearms') == 1 and s.rd8(ARMED) == 1
    else:
        assert 'touch_arm' not in k and s.field('rearms') == 0 and s.rd8(ARMED) == 0, 'norearm: 0x1848 never called; ARMED cleared so the loop re-arms when awake'
    assert s.field('busy_pre') == 0 and s.field('pre_ms10') == 0
    assert s.field('busy_first') == 1 and s.field('busy_ms10') == (200 >> 3) and s.field('busy_timeouts') == 0 and s.field('busy_fallbacks') == 0
    assert s.field('sleep_hits') == 1 and s.field('sleep_draws') == 1 and s.field('skips') == 0 and s.field('last_path') == 1
    assert s.field('saver_active') == 1 and s.field('lptim_since_draw') == 0 and s.field('draw_target') == 0x34
    assert s.field('cur_slot') == 0 and s.field('picks') == 1 and s.field('map_at_draw') == 1 and s.field('disabled') == 0
    assert (s.field('fb_lo'), s.field('fb_hi')) == (0x80, 0x1f) and s.field('deferred') == 0 and s.field('defers') == 0
    assert s.rd(SHOWN) == 0x34 and s.rd(TARGET) == 0x34 and s.rd(0x20006bc8) == 1000, 'no LVGL state, no idle-stamp restart'
    assert 0x1e160 not in F and 0x19a9c not in F and 'dispatch' not in k, 'no stock busy-wait (RST on timeout), no page dispatch, no lv_task_handler'
    assert s.rd8(PARTIAL_CNT) == FULL_AT, 'v15: the sleep blit arms the counter: the page that replaces the saver is a FULL update'
    # second decision with the image already in the framebuffer: silent sleep, no redraw (v15: after the bounded pre-wait)
    s.call(ss['ss_sleep_hook'])
    assert s.events == ['busy', 'sleep'] and s.field('sleep_draws') == 1 and s.field('skips') == 1 and s.field('last_path') == 3 and s.field('saver_active') == 1
    # v15: the skip path waits for a refresh still in flight before the stock deep-sleep
    s.busy_polls = 6; s.call(ss['ss_sleep_hook'])
    assert s.kinds().count('busy') == 7 and s.kinds()[-1] == 'sleep' and s.field('skips') == 2 and s.field('busy_pre') == 1
    # a stock refresh still running when we arrive: pre-wait first, then the write
    s = fresh(); s.busy_polls = 10; s.refresh_busy = 4
    s.call(ss['ss_sleep_hook'])
    k = s.kinds()
    i_w = k.index('flush_begin')
    assert k[:i_w].count('busy') == 11 and k[:i_w].count('delay') == 10 and wrap_ok(s, i_w) and k[-1] == 'sleep', s.events
    assert s.field('busy_pre') == 1 and s.field('pre_ms10') == (50 >> 3) and s.field('busy_first') == 1 and s.field('busy_ms10') == (20 >> 3)
    # BUSY never reads high after the refresh command: fixed 3 s fallback, still sleeps
    s = fresh(); s.refresh_busy = 0
    s.call(ss['ss_sleep_hook'])
    assert s.field('busy_first') == 0 and s.field('busy_fallbacks') == 1 and ('delay', 3000) in s.events and s.kinds()[-1] == 'sleep'
    # BUSY never releases: the wait is capped (4000 ms) and still proceeds to sleep
    s = fresh(); s.refresh_busy = 10 ** 6
    s.call(ss['ss_sleep_hook'])
    assert s.kinds()[-1] == 'sleep' and s.field('busy_timeouts') == 1 and s.field('busy_ms10') == 255 and s.field('busy_fallbacks') == 0
    assert 4000 <= s.tick - 1000 - 5 - 150 <= 4010, s.tick
    # the stock panel-absent flag must NOT skip our wait
    s = fresh(); s.wr8(PANEL_ABSENT, 1); s.refresh_busy = 3
    s.call(ss['ss_sleep_hook'])
    assert s.kinds().count('busy') == 1 + 4 and s.kinds()[-1] == 'sleep'
    # pending wake flags raised during the refresh are scrubbed after the settle, before sleeping
    s = fresh(); s.refresh_busy = 3; s.wr8(WAKE_TOUCH, 1); s.wr8(WAKE_RXEDGE, 1)
    s.call(ss['ss_sleep_hook'])
    assert s.rd8(WAKE_TOUCH) == 0 and s.rd8(WAKE_RXEDGE) == 0 and s.field('scrubbed') == 1 and s.kinds()[-1] == 'sleep'
    # a touch-INT event that fired (PA2): consumed; rearm build re-arms once more, norearm leaves ARMED 0
    s = fresh(); s.refresh_busy = 3; s.wr8(PA2_EVENT, 1); s.wr8(ARMED, 0)
    s.call(ss['ss_sleep_hook'])
    assert s.rd8(PA2_EVENT) == 0 and s.field('pa2_scrubbed') == 1 and s.kinds()[-1] == 'sleep'
    if rearm:
        assert s.kinds().count('touch_arm') == 2 and s.rd8(ARMED) == 1 and s.field('rearms') == 1
    else:
        assert 'touch_arm' not in s.kinds() and s.rd8(ARMED) == 0
    # main talked during the blit: the sleep is deferred ONE pass so the loop can see the frame
    s = fresh(); s.refresh_busy = 3; s.cpu.mem_write(RX_COUNT, b'\x07\x00')
    r = s.call(ss['ss_sleep_hook'])
    assert 'sleep' not in s.kinds() and s.field('deferred') == 1 and s.field('defers') == 1 and s.field('sleep_draws') == 1
    r = s.call(ss['ss_sleep_hook'])                  # next decision: image already in place -> skip -> sleep
    assert s.events == ['busy', 'sleep'] and s.field('deferred') == 0 and s.field('skips') == 1
    s = fresh(deferred=1); s.refresh_busy = 3; s.cpu.mem_write(RX_COUNT, b'\x07\x00')
    s.call(ss['ss_sleep_hook'])
    assert s.kinds()[-1] == 'sleep' and s.field('defers') == 0, 'at most one deferral per sleep attempt'
    # no image -> stock sleep only, nothing written (v15: after the pre-wait, which also covers the saver-off paths)
    s = Screen(image); s.seed_state(); s.cpu.mem_write(FB, b'\x55' * FB_BYTES)
    s.call(ss['ss_sleep_hook'])
    assert s.events == ['busy', 'sleep'] and s.fb() == b'\x55' * FB_BYTES and s.field('sleep_draws') == 0 and s.field('map_at_draw') == 0
    s = Screen(image); s.seed_state(); s.busy_polls = 3; s.call(ss['ss_sleep_hook'])
    assert s.kinds().count('busy') == 4 and s.kinds()[-1] == 'sleep', 'no image: still never deep-sleeps the panel mid-refresh'
    checks += 1

    # --- v15: the page-dispatch hook -- pre-wait, the stock dispatcher with r4/r5 preserved, the restore forced FULL ---
    def dispatch(s, state, full=0, sync=0):
        s.disp_full, s.disp_sync = full, sync
        s.cpu.reg_write(UC_ARM_REG_R4, 0x200003f4); s.cpu.reg_write(UC_ARM_REG_R1, 0x77)
        s.call(ss['ss_dispatch_hook'], r0=state)
        assert s.cpu.reg_read(UC_ARM_REG_R4) == 0x200003f4, '0xe65c keeps &shown in r4 across the call'
    s = Screen(image); s.seed_state(); s.wr8(PARTIAL_CNT, 5); s.wr8(SYNC_SKIP, 0); s.wr8(FULL_FLAG, 1)
    dispatch(s, 0x5b)                                # an ordinary page change: the stock's own policy stands
    assert s.events == ['busy', ('dispatch', 0x5b)], s.events
    assert s.rd8(PARTIAL_CNT) == 5 and s.rd8(SYNC_SKIP) == 0 and s.rd8(FULL_FLAG) == 0
    assert s.field('disp_calls') == 1 and s.field('disp_forced') == 0 and s.field('disp_busy_pre') == 0 and s.field('rst_pending') == 0
    s.seed_state(rst_pending=1); s.wr8(PARTIAL_CNT, 5)
    dispatch(s, 0x5b)                                # the restore's dispatch: partial policy + sync wait recorded, then overridden
    assert s.events == ['busy', ('dispatch', 0x5b)] and s.rd8(PARTIAL_CNT) == FULL_AT and s.rd8(SYNC_SKIP) == 1 and s.rd8(FULL_FLAG) == 0
    assert s.field('rst_policy') == 0 and s.field('rst_sync') == 0 and s.field('rst_pending') == 0 and s.field('disp_forced') == 1
    # the model's 0x1858 now does ONE full update (counter -> 0), the following flush is partial again (stock policy resumes)
    s.wr8(FULL_FLAG, 0); s.call(ss['ss_command'], payload=bytes([0x08, 96])); assert s.reply[:2] == bytes([0x80 | 96, FULL_AT])
    s.seed_state(rst_pending=1); s.wr8(PARTIAL_CNT, 0)
    dispatch(s, 0x34, full=1, sync=1)                # a full-policy page: recorded as such, lever still armed (harmless: 0xc030 zeroes it)
    assert s.field('rst_policy') == 1 and s.field('rst_sync') == 1 and s.rd8(PARTIAL_CNT) == FULL_AT and s.rd8(SYNC_SKIP) == 1
    # a refresh in flight at dispatch time: wait it out first (bounded, no RST), then build
    s = Screen(image); s.seed_state(); s.busy_polls = 12
    dispatch(s, 0x5b)
    k = s.kinds(); i_d = k.index('dispatch')
    # 13 reads: the hook's own first read, then wait_busy's first read and 11 delay+read polls (12 busy + 1 idle)
    assert k[:i_d].count('busy') == 13 and k[:i_d].count('delay') == 11 and s.field('disp_busy_pre') == 1 and s.field('disp_ms10') == (55 >> 3) and s.field('disp_timeouts') == 0
    s = Screen(image); s.seed_state(); s.busy_polls = 10 ** 6
    dispatch(s, 0x5b)
    assert ('dispatch', 0x5b) in s.events and s.field('disp_timeouts') == 1 and s.field('disp_ms10') == 255 and 4000 <= s.tick - 1000 <= 4010
    assert 0x1e160 not in F and 0x1e11c not in F, 'the dispatch wait is ours: never the stock wait whose timeout pulses RST'
    checks += 1

    # --- v15: the post-restore grace -- no NEW saver image for GRACE_MS of awake time after a genuine restore ---
    s = fresh(grace_armed=1, rst_tick=1000 - 500)    # tick 1000: the restore was 500 ms of awake time ago, fb holds the page
    assert s.call(ss['ss_sleep_hook']) == 0 and s.events == ['busy'], 'deferred: only the pre-wait read, no draw, no sleep'
    assert s.field('grace_defers') == 1 and s.field('sleep_hits') == 0 and s.field('sleep_draws') == 0 and s.field('picks') == 0
    assert s.field('quiet_ms10') == (500 >> 3) and s.field('grace_armed') == 1 and s.fb() == b'\x55' * FB_BYTES
    for _ in range(3):
        s.tick += 100; s.call(ss['ss_sleep_hook'])
    assert s.field('grace_defers') == 4 and s.field('quiet_ms10') == (500 >> 3), 'quiet_ms10 is sampled once, at the first decision'
    s.tick = 1000 - 500 + GRACE_MS                   # the grace expires: draw + sleep as before
    s.call(ss['ss_sleep_hook'])
    assert s.kinds()[-1] == 'sleep' and s.field('sleep_draws') == 1 and s.field('grace_armed') == 0 and s.fb() == want
    # inside the grace but the panel already holds the current slot (e.g. after SHOW): silent sleep, grace untouched
    s = fresh(grace_armed=1, rst_tick=1000 - 500, cur_slot=0); s.cpu.mem_write(FB, want)
    s.call(ss['ss_sleep_hook'])
    assert s.events == ['busy', 'sleep'] and s.field('skips') == 1 and s.field('grace_defers') == 0
    # saturation: quiet_ms10 caps at 255, grace_defers at 255
    s = fresh(grace_armed=1, rst_tick=(1000 - 5000) & 0xffffffff, grace_defers=255); s.call(ss['ss_sleep_hook'])   # the tick wraps: unsigned dt = 5000
    assert s.field('quiet_ms10') == 255 and s.field('grace_defers') == 255 and s.events == ['busy']
    # a saver draw supersedes a restore that never got its dispatch
    s = fresh(rst_pending=1); s.call(ss['ss_sleep_hook']); assert s.field('rst_pending') == 0 and s.kinds()[-1] == 'sleep'
    checks += 1

    # --- v13: six slots, the picker, SHOW <slot>, THRESHOLD, MODE, CLEAR, a dead upper slot ---
    pix = {i: random_pixels(100 + i) for i in range(NSLOTS)}
    inv = {i: inverted(pix[i]) for i in range(NSLOTS)}

    def which(s):
        fb = s.fb()
        hits = [i for i in range(NSLOTS) if fb == inv[i]]
        return hits[0] if hits else None

    def multi(filled, **kw):
        s = Screen(image); s.seed_state(**kw)
        for i in filled:
            s.put_slot(pix[i], i)
        s.wr(SHOWN, 0x34); s.wr(TARGET, 0x34); s.wr8(BL_DONE, 1); s.wr8(FULL_FLAG, 0); s.wr(0x20006bc8, 1000)
        s.cpu.mem_write(FB, b'\x55' * FB_BYTES); s.cpu.mem_write(RX_COUNT, b'\x00\x00'); s.refresh_busy = 3
        return s

    # BEGIN/DATA/COMMIT into slots 3 and 5 land ONLY in that slot; slot 0 untouched; the map reports all three
    s = Screen(image); s.seed_state(); s.put_slot(pix[0], 0)
    for slot in (3, 5):
        s.call(ss['ss_command'], payload=bytes([0x00, slot]) + struct.pack('<H', IMG_BYTES))
        assert s.events == ['ack'] and s.field('rx_active') == 1 and s.field('rx_slot') == slot
        erases = [o for o in s.flash_ops if o[0] == 'erase']
        assert len(erases) == 16 and erases[0][1] == slot_base(slot) and erases[-1][1] == slot_base(slot) + SLOT_SIZE - 512
        check_flash_window(s.flash_ops, slot)
        blob = blob_of(pix[slot])
        for off in range(0, IMG_BYTES, 128):
            s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', off) + blob[off:off + 128])
            assert s.events == ['ack'], (slot, off, s.events)
            check_flash_window(s.flash_ops, slot)
        s.call(ss['ss_command'], payload=bytes([0x02]) + struct.pack('<I', zlib.crc32(blob) & 0xffffffff))
        assert s.events == ['ack'] and s.slot_present(slot) and s.field('rx_active') == 0
        check_flash_window(s.flash_ops, slot)
        assert bytes(s.cpu.mem_read(slot_base(slot) + 8, IMG_BYTES)) == blob
    assert s.slot_map() == 0b101001 and bytes(s.cpu.mem_read(SLOT + 16, FB_BYTES)) == pix[0]
    s.call(ss['ss_command'], payload=bytes([0x08, 0])); assert s.reply[:2] == bytes([0x80, 0b101001]), s.reply
    s.call(ss['ss_command'], payload=bytes([0x07])); assert s.reply[2] == 0b101001
    # DATA never writes outside the BEGIN slot, even at the top of the image
    s.call(ss['ss_command'], payload=bytes([0x00, 5]) + struct.pack('<H', IMG_BYTES))
    s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', IMG_BYTES - 8) + bytes(8))
    assert s.events == ['ack']; check_flash_window(s.flash_ops, 5)
    s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', IMG_BYTES - 8) + bytes(9))
    assert s.events == ['nak'] and not [o for o in s.flash_ops if o[0] == 'prog']
    for op in s.flash_ops:
        assert SLOT <= op[1] < SLOTS_END
    checks += 1

    # --- v14 forensics: BEGIN resets the diagnostics; a DROPPED DATA frame -> COMMIT naks with diag 0x10 (received stream AND
    #     flash both wrong), rx_active stays 1, no header; re-sending the frame + COMMIT -> present, diag 0x13, counters exact ---
    s = Screen(image); s.seed_state(rx_crc=0xdeadbeef, rx_frames=9, rx_bytes=9, err_addr=1, err_code=1, diag=1, slock_tries=1)
    blob = blob_of(pix[2])
    s.call(ss['ss_command'], payload=bytes([0x00, 2]) + struct.pack('<H', IMG_BYTES))
    assert s.events == ['ack'] and s.rd(ST_ADDR + FIELDS['rx_crc']) == 0xffffffff and s.field('diag') == 0 and s.rd(ST_ADDR + FIELDS['err_addr']) == 0
    assert struct.unpack('<H', s.cpu.mem_read(ST_ADDR + FIELDS['rx_frames'], 2))[0] == 0 and s.field('slock_tries') == 1
    unlocks = [o for o in s.flash_ops if o[0] == 'unlock']; erases = [o for o in s.flash_ops if o[0] == 'erase']
    assert len(unlocks) == 16 and len(erases) == 16, 'one verified unlock per erased sector'
    for off in range(0, IMG_BYTES, 128):
        if off == 4096:
            continue                                                   # the dropped frame
        s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', off) + blob[off:off + 128])
        assert s.events == ['ack']
    s.call(ss['ss_command'], payload=bytes([0x02]) + struct.pack('<I', zlib.crc32(blob) & 0xffffffff))
    assert s.events == ['nak'] and s.field('diag') == 0x10 and s.field('rx_active') == 1 and s.field('last_err') == 0 and not s.slot_present(2)
    assert not [o for o in s.flash_ops if o[0] == 'prog'], 'no header write on a crc mismatch'
    s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', 4096) + blob[4096:4096 + 128])
    assert s.events == ['ack']
    s.call(ss['ss_command'], payload=bytes([0x02]) + struct.pack('<I', zlib.crc32(blob) & 0xffffffff))
    # the lone late frame completes the FLASH (-> present) but the arrival-order stream crc is now out of order: diag 0x12
    assert s.events == ['ack'] and s.slot_present(2) and s.field('diag') == 0x12 and s.field('rx_active') == 0
    assert struct.unpack('<HH', s.cpu.mem_read(ST_ADDR + FIELDS['rx_frames'], 4)) == (64, IMG_BYTES)
    assert bytes(s.cpu.mem_read(slot_base(2) + 8, IMG_BYTES)) == blob
    check_flash_window(s.flash_ops, 2)
    # a full re-send pass (the tool's retry) starts at offset 0, which restarts the stream counters -> diag 0x13, exact counts
    s = Screen(image); s.seed_state(); blob5 = blob_of(pix[5])
    s.call(ss['ss_command'], payload=bytes([0x00, 5]) + struct.pack('<H', IMG_BYTES))
    for off in range(0, IMG_BYTES, 128):
        if off != 2048:
            s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', off) + blob5[off:off + 128])
    s.call(ss['ss_command'], payload=bytes([0x02]) + struct.pack('<I', zlib.crc32(blob5) & 0xffffffff))
    assert s.events == ['nak'] and s.field('diag') == 0x10 and s.field('rx_active') == 1
    for off in range(0, IMG_BYTES, 128):
        s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', off) + blob5[off:off + 128])
        assert s.events == ['ack'], 're-programming identical bytes / filling the dropped frame acks'
    s.call(ss['ss_command'], payload=bytes([0x02]) + struct.pack('<I', zlib.crc32(blob5) & 0xffffffff))
    assert s.events == ['ack'] and s.slot_present(5) and s.field('diag') == 0x13 and s.field('rx_active') == 0
    assert struct.unpack('<HH', s.cpu.mem_read(ST_ADDR + FIELDS['rx_frames'], 4)) == (64, IMG_BYTES)
    assert (~s.rd(ST_ADDR + FIELDS['rx_crc'])) & 0xffffffff == zlib.crc32(blob5) & 0xffffffff, 'rx_crc state finishes to the blob crc'
    assert bytes(s.cpu.mem_read(slot_base(5) + 8, IMG_BYTES)) == blob5
    check_flash_window(s.flash_ops, 5)
    # a second COMMIT after success naks (rx_active 0), so a stale retry can never re-write a header
    s.call(ss['ss_command'], payload=bytes([0x02]) + struct.pack('<I', zlib.crc32(blob) & 0xffffffff)); assert s.events == ['nak']
    # retention: every frame received and readback-verified, but the flash reads back differently at COMMIT -> diag 0x11, nak, rx_active 1
    s = Screen(image); s.seed_state()
    s.call(ss['ss_command'], payload=bytes([0x00, 1]) + struct.pack('<H', IMG_BYTES))
    for off in range(0, IMG_BYTES, 128):
        s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', off) + blob[off:off + 128])
    s.cpu.mem_write(slot_base(1) + 8 + 5000, b'\xff')
    s.call(ss['ss_command'], payload=bytes([0x02]) + struct.pack('<I', zlib.crc32(blob) & 0xffffffff))
    assert s.events == ['nak'] and s.field('diag') == 0x11 and s.field('rx_active') == 1 and s.field('last_err') == 0 and not s.slot_present(1)
    s.call(ss['ss_command'], payload=bytes([0x08, 74])); assert s.reply[:2] == bytes([0x80 | 74, 0x11])
    # the SLOCK bit never sets (RM Step 7 fails) -> BEGIN naks: last_err 1, err_code 0xa, err_addr = the slot base, NO erase issued
    s = Screen(image); s.seed_state(); s.slock_dead = True
    s.call(ss['ss_command'], payload=bytes([0x00, 0]) + struct.pack('<H', IMG_BYTES))
    assert s.events == ['nak'] and s.field('last_err') == 1 and s.field('err_code') == 0xa and s.rd(ST_ADDR + FIELDS['err_addr']) == SLOT
    assert s.field('slock_tries') == 0 and s.field('rx_active') == 0 and not [o for o in s.flash_ops if o[0] == 'erase']
    assert len([o for o in s.flash_ops if o[0] == 'unlock']) == 8, '8 unlock attempts before giving up'
    # an erase that leaves the data (protected sector, driver still returns 0) -> code 8 at that sector, last_err 1
    s = Screen(image); s.seed_state(); s.put_slot(pix[0], 0); s.erase_void_from = SLOT + 0x800
    s.call(ss['ss_command'], payload=bytes([0x00, 0]) + struct.pack('<H', IMG_BYTES))
    assert s.events == ['nak'] and s.field('last_err') == 1 and s.field('err_code') == 8 and s.rd(ST_ADDR + FIELDS['err_addr']) == SLOT + 0x800
    assert len([o for o in s.flash_ops if o[0] == 'erase']) == 5, 'sectors 0..3 erased and verified, sector 4 voided -> stop'
    # a programmer readback mismatch names the address and code 1, last_err 2
    s = Screen(image); s.seed_state(); s.prog_fail_at = slot_base(3) + 8 + 300
    s.call(ss['ss_command'], payload=bytes([0x00, 3]) + struct.pack('<H', IMG_BYTES))
    s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', 256) + blob[256:384])
    assert s.events == ['nak'] and s.field('last_err') == 2 and s.field('err_code') == 1 and s.rd(ST_ADDR + FIELDS['err_addr']) == slot_base(3) + 8 + 256
    assert s.field('rx_active') == 0 and struct.unpack('<H', s.cpu.mem_read(ST_ADDR + FIELDS['rx_frames'], 2))[0] == 0
    # the old v13 dead-slot model (driver returns 9) still reports 7/last_err 1 and never becomes present
    s = Screen(image); s.seed_state(); s.dead_from = slot_base(4)
    s.call(ss['ss_command'], payload=bytes([0x00, 4]) + struct.pack('<H', IMG_BYTES))
    assert s.events == ['nak'] and s.field('last_err') == 1 and s.field('err_code') == 7 and s.rd(ST_ADDR + FIELDS['err_addr']) == slot_base(4)
    # MMIO readbacks: IFR / SLOCK2 hi / SLOCK3 lo,hi / CR reflect what the (modelled) unlock left behind
    s = Screen(image); s.seed_state(); s.put_slot(pix[0], 0)
    s.call(ss['ss_command'], payload=bytes([0x00, 0]) + struct.pack('<H', IMG_BYTES))
    s.call(ss['ss_command'], payload=bytes([0x08, 79])); assert s.reply[:2] == bytes([0x80 | 79, 0x80]), 'last unlock of slot 0 = SLOCK2 bit 31'
    s.call(ss['ss_command'], payload=bytes([0x00, 2]) + struct.pack('<H', IMG_BYTES))
    s.call(ss['ss_command'], payload=bytes([0x08, 80])); assert s.reply[:2] == bytes([0x80 | 80, 0x80]), 'last unlock of slot 2 = SLOCK3 bit 7'
    s.call(ss['ss_command'], payload=bytes([0x08, 83])); assert s.reply[:2] == bytes([0x80 | 83, 0xff]), 'slot 2 first DATA byte still erased'
    checks += 1

    # SHOW <slot> blits that slot; SHOW alone re-blits the current slot or picks one; an absent slot naks
    s = multi([0, 2, 5])
    s.call(ss['ss_command'], payload=bytes([0x03, 5]))
    assert s.events[-1] == 'ack' and which(s) == 5 and s.field('cur_slot') == 5 and s.field('show_blits') == 1
    s.call(ss['ss_command'], payload=bytes([0x03, 4]))
    assert s.events == ['nak'] and which(s) == 5 and s.field('cur_slot') == 5
    s.call(ss['ss_command'], payload=bytes([0x03]))
    assert s.events[-1] == 'ack' and which(s) == 5, 'SHOW without a slot re-blits the current slot'
    s = multi([2, 4]); s.call(ss['ss_command'], payload=bytes([0x03]))
    assert s.events[-1] == 'ack' and which(s) in (2, 4) and s.field('cur_slot') == which(s)
    s.call(ss['ss_sleep_hook'])                    # the next sleep finds the current slot on the panel: silent -- after waiting out SHOW's refresh (v15)
    k = s.kinds()
    assert k[-1] == 'sleep' and k.count('busy') == 4 and k.count('delay') == 3 and 'epd_write' not in k and s.field('skips') == 1 and s.field('picks') == 0
    checks += 1

    # random picker: only filled slots, never the slot on the panel while another is filled, every filled slot reached
    filled = [0, 2, 5]
    s = multi(filled); seen, prev = set(), None
    for k in range(60):
        s.cpu.mem_write(FB, b'\x55' * FB_BYTES)   # a genuine wake re-dispatched the normal page
        s.tick += 137 * (k + 1) + 31; s.wr(BATT_RAW, 3400 + (k * 7) % 13)
        s.call(ss['ss_sleep_hook'])
        assert s.kinds()[-1] == 'sleep' and ('epd_refresh', 1) in s.events
        got = which(s)
        assert got in filled, f'picked an absent slot: {got}'
        assert got != prev, f'repeated slot {got} with {len(filled)} filled'
        assert s.field('cur_slot') == got and s.field('map_at_draw') == 0b100101
        seen.add(got); prev = got
    assert seen == set(filled), f'random picker never reached {set(filled) - seen}'
    assert s.field('picks') == 60 and s.field('sleep_draws') == 60 and s.field('skips') == 0
    # sequential picker: filled slots in order, wrapping; starts after the current slot
    s = multi(filled, mode=1); order = []
    for k in range(7):
        s.cpu.mem_write(FB, b'\x55' * FB_BYTES); s.call(ss['ss_sleep_hook']); order.append(which(s))
    assert order == [0, 2, 5, 0, 2, 5, 0], order
    s = multi(filled, mode=1, cur_slot=2); s.call(ss['ss_sleep_hook']); assert which(s) == 5
    # a single filled slot is drawn again and again (v12 behaviour)
    s = multi([3])
    for k in range(3):
        s.cpu.mem_write(FB, b'\x55' * FB_BYTES); s.tick += 999; s.call(ss['ss_sleep_hook']); assert which(s) == 3
    # the slot on the panel is skipped silently; another slot is drawn only after the panel changed
    s = multi(filled); s.call(ss['ss_sleep_hook']); first = which(s)
    s.call(ss['ss_sleep_hook']); assert s.events == ['busy', 'sleep'] and which(s) == first and s.field('skips') == 1
    # no filled slot at all: stock sleep, nothing written
    s = multi([]); s.call(ss['ss_sleep_hook'])
    assert s.events == ['busy', 'sleep'] and s.fb() == b'\x55' * FB_BYTES and s.field('map_at_draw') == 0 and s.field('picks') == 0
    checks += 1

    # THRESHOLD 0 disables (stock sleep, nothing drawn); any other value re-enables; aliases untouched; MODE
    s = multi(filled)
    s.call(ss['ss_command'], payload=bytes([0x04, 0, 0])); assert s.events == ['ack'] and s.field('disabled') == 1
    s.call(ss['ss_sleep_hook']); assert s.events == ['busy', 'sleep'] and s.fb() == b'\x55' * FB_BYTES and s.field('sleep_hits') == 1
    s.call(ss['ss_command'], payload=bytes([0x04]) + struct.pack('<H', 300))
    assert s.events == ['ack'] and s.field('disabled') == 0 and (s.field('thr_lo'), s.field('thr_hi')) == (0x2c, 1)
    s.call(ss['ss_sleep_hook']); assert s.kinds()[-1] == 'sleep' and which(s) in filled
    s.call(ss['ss_command'], payload=bytes([0x04, 0x05, 0xfe])); assert s.events == ['nak'] and s.field('disabled') == 0
    s.call(ss['ss_command'], payload=bytes([0x08, 58])); assert s.reply[:2] == bytes([0x80 | 58, 0])
    s.call(ss['ss_command'], payload=bytes([0x06, 1])); assert s.events == ['ack'] and s.field('mode') == 1
    s.call(ss['ss_command'], payload=bytes([0x06, 0, 3])); assert s.events == ['ack'] and s.field('mode') == 0   # main v1 may append a slot byte
    s.call(ss['ss_command'], payload=bytes([0x06, 2])); assert s.events == ['nak'] and s.field('mode') == 0
    assert not s.flash_ops
    checks += 1

    # CLEAR: the header sector of a present slot; all = every present slot; absent slots untouched
    s = multi(filled, cur_slot=2); s.cpu.mem_write(FB, inv[2])
    s.call(ss['ss_command'], payload=bytes([0x05, 2]))
    assert s.events == ['ack'] and [o for o in s.flash_ops if o[0] == 'erase'] == [('erase', slot_base(2))]
    assert s.slot_map() == 0b100001 and not s.slot_present(2)
    s.call(ss['ss_command'], payload=bytes([0x05, 2])); assert s.events == ['ack'] and not s.flash_ops   # already clear: no flash op
    s.call(ss['ss_sleep_hook'])                    # the cleared slot was on the panel: draw another one
    assert s.kinds()[-1] == 'sleep' and which(s) in (0, 5) and s.field('skips') == 0
    s.call(ss['ss_command'], payload=bytes([0x05, 0xff]))
    assert s.events == ['ack'] and sorted(o[1] for o in s.flash_ops if o[0] == 'erase') == [slot_base(0), slot_base(5)]
    assert s.slot_map() == 0
    s.cpu.mem_write(FB, b'\x55' * FB_BYTES); s.call(ss['ss_sleep_hook']); assert s.events == ['busy', 'sleep'] and s.fb() == b'\x55' * FB_BYTES
    checks += 1

    # a DEAD upper slot (erase/program refused from 0x36000 = slot 4 on): BEGIN naks, nothing becomes present, never picked
    s = multi([0, 2]); s.dead_from = slot_base(4)
    s.call(ss['ss_command'], payload=bytes([0x00, 4]) + struct.pack('<H', IMG_BYTES))
    assert s.events == ['nak'] and s.field('rx_active') == 0 and s.field('last_err') == 1 and s.slot_map() == 0b101
    s.call(ss['ss_command'], payload=bytes([0x01]) + struct.pack('<H', 0) + bytes(16)); assert s.events == ['nak']
    s.call(ss['ss_command'], payload=bytes([0x00, 5]) + struct.pack('<H', IMG_BYTES)); assert s.events == ['nak']
    s.call(ss['ss_command'], payload=bytes([0x00, 3]) + struct.pack('<H', IMG_BYTES)); assert s.events == ['ack']  # slot 3 still fine
    for k in range(20):
        s.cpu.mem_write(FB, b'\x55' * FB_BYTES); s.tick += 61 * k + 7; s.call(ss['ss_sleep_hook'])
        assert which(s) in (0, 2)
    assert s.field('picks') == 20 and s.slot_map() == 0b101
    checks += 1

    # --- activity: restore (SHOWN := 0xff) only for genuine input while the saver is shown ---
    def wake(s, code, mode, src=1):
        s.cpu.mem_write(0x20007000 + 12, struct.pack('<I', CALLER_LR[code]))   # 0xbe10's pushed lr
        s.wr8(LP_MODE, mode); s.wr8(RX_EDGE, src); s.cpu.reg_write(UC_ARM_REG_R4, 0x77)
        s.call(ss['ss_activity_hook'])
        assert s.cpu.reg_read(UC_ARM_REG_R0) == 0x20006bcc and s.cpu.reg_read(UC_ARM_REG_R4) == 0
    def shown(s):
        s.seed_state(saver_active=1); s.wr(TARGET, 0x34); s.wr(SHOWN, 0x34)
    s = Screen(image); shown(s)
    for _ in range(3):                                # PA8-line blips right after the draw, asleep
        wake(s, 3, 1, src=0)
    assert s.rd(SHOWN) == 0x34 and s.field('restores') == 0 and s.field('suppressed') == 3 and s.field('saver_active') == 1
    wake(s, 3, 0, src=0); wake(s, 1, 0); wake(s, 5, 1); wake(s, 4, 0)   # awake blip / first-run / unknown / touch while awake
    assert s.rd(SHOWN) == 0x34 and s.field('restores') == 0 and s.field('suppressed') == 7
    # LPTIM wake through the real asm hook expires the window: the next PA8-line event restores
    s.call(ss['ss_lptim_hook']); assert s.events == ['lptim'] and s.field('lptim_wakes') == 1 and s.field('lptim_since_draw') == 1
    wake(s, 3, 2, src=0)
    assert s.rd(SHOWN) == 0xff and s.rd(TARGET) == 0x34 and s.field('restores') == 1 and s.field('restore_caller') == 3 and s.field('rst_src') == 0
    assert s.field('saver_active') == 0
    # the touch INT (PA2) from sleep restores at once
    s = Screen(image); shown(s); wake(s, 4, 1, src=0)
    assert s.rd(SHOWN) == 0xff and s.field('restores') == 1 and s.field('restore_caller') == 4
    # UART-sourced activity restores immediately, even inside the window
    s = Screen(image); shown(s); wake(s, 3, 1, src=1)
    assert s.rd(SHOWN) == 0xff and s.field('restores') == 1 and s.field('rst_src') == 1
    s = Screen(image); shown(s); wake(s, 2, 1, src=0)
    assert s.rd(SHOWN) == 0xff and s.field('restores') == 1 and s.field('restore_caller') == 2
    # v15: a genuine restore arms the dispatch override + the grace clock and snapshots the raw wake state (before 0xbe10's RST pulse)
    s = Screen(image); shown(s); s.tick = 4321; s.wr8(SLEPT_FLAG, 1); s.wr8(LPTIM_FLAG, 1); s.wr8(PA2_EVENT, 0); s.wr8(ARMED, 0); s.busy_polls = 1
    wake(s, 2, 1, src=0)
    assert s.field('rst_pending') == 1 and s.field('grace_armed') == 1 and s.rd(ST_ADDR + FIELDS['rst_tick']) == 4321 and s.field('quiet_ms10') == 0
    assert s.field('wake_snap') == (0 | 2 | 4 | 8 | 0 | 0 | (1 << 6)), hex(s.field('wake_snap'))
    s = Screen(image); shown(s); s.wr8(SLEPT_FLAG, 0); s.wr8(LPTIM_FLAG, 0); s.wr8(PA2_EVENT, 1); s.wr8(ARMED, 1)
    wake(s, 3, 2, src=1)
    assert s.field('wake_snap') == (1 | 16 | 32 | (2 << 6)) and s.field('rst_pending') == 1
    # ... but a suppressed blip arms nothing
    s = Screen(image); shown(s); wake(s, 3, 1, src=0)
    assert s.field('suppressed') == 1 and s.field('rst_pending') == 0 and s.field('grace_armed') == 0 and s.field('act_caller') == 3
    # device AWAKE (mode 0): never restores, even with the UART source
    s = Screen(image); shown(s); wake(s, 3, 0, src=1); wake(s, 2, 0)
    assert s.rd(SHOWN) == 0x34 and s.field('restores') == 0 and s.field('suppressed') == 2
    # saver not on the panel: nothing changes, counted as ignored
    s = Screen(image); s.seed_state(saver_active=0); s.wr(TARGET, 0x34); s.wr(SHOWN, 0x34)
    wake(s, 4, 1, src=0); wake(s, 2, 1)
    assert s.rd(SHOWN) == 0x34 and s.field('restores') == 0 and s.field('ignored') == 2 and s.field('ignored_caller') == 2
    checks += 1
    return checks


class MainConfig(FirmwareConfig):
    def __init__(self, image):
        super().__init__(image)
        self.sends, self.notifies, self.dispatched, self.in_rx = [], [], [], False

    def hook(self, cpu, address, size, _):
        if address == 0x81a540:
            r2, r3, sp = cpu.reg_read(UC_ARM_REG_R2), cpu.reg_read(UC_ARM_REG_R3), cpu.reg_read(UC_ARM_REG_SP)
            ptr, n = struct.unpack('<II', cpu.mem_read(sp + 4, 8))
            self.sends.append((r2, r3, bytes(cpu.mem_read(ptr, n)) if 0 < n <= 512 else b''))
            cpu.reg_write(UC_ARM_REG_R0, 0); cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR)); return
        if address == 0x818382 and self.in_rx:
            r0, r1 = cpu.reg_read(UC_ARM_REG_R0), cpu.reg_read(UC_ARM_REG_R1)
            self.notifies.append((r0, r1, bytes(cpu.mem_read(r0, r1))))
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR)); return
        if address == 0x81aaf4:
            self.dispatched.append(cpu.reg_read(UC_ARM_REG_R0)); cpu.emu_stop(); return
        super().hook(cpu, address, size, _)

    def rx(self, hook_va, frame):
        self.notifies, self.dispatched, self.in_rx = [], [], True
        self.cpu.mem_write(0x20E100, frame)
        self.cpu.reg_write(UC_ARM_REG_R0, 0x20E100); self.cpu.reg_write(UC_ARM_REG_SP, 0x20F000)
        self.cpu.reg_write(UC_ARM_REG_LR, 0x830001)
        self.cpu.emu_start(hook_va | 1, 0x830000, count=20000)


def verify_main(image, syms):
    checks = 0
    c = MainConfig(image)
    assert c.command(b'W\x0c') == b'\x0c\x01SS02' and not c.sends
    for good in (bytes([0x00, 0]) + struct.pack('<H', IMG_BYTES), bytes([0x01]) + struct.pack('<H', 0) + bytes(64),
                 bytes([0x02]) + bytes(4), bytes([0x03]), bytes([0x07]), bytes([0x08, 15]), bytes([0x04, 0x0f, 0xff])):
        c = MainConfig(image)
        assert c.command(b'W\x0c' + good) == b'\x0c\x01' and c.sends[0][1] == 0x50 and c.sends[0][2] == good
    for bad in (bytes([0x00, 9]) + struct.pack('<H', IMG_BYTES), bytes([0x02]) + bytes(2), bytes([0x0a])):
        c = MainConfig(image)
        assert c.command(b'W\x0c' + bad) == b'\x0c\x02' and not c.sends
    checks += 1
    hook = int(syms['ss_rx_hook'], 16)
    body = bytes([0xa6, 0x6a, 0x50, 0]) + struct.pack('<H', 3) + b'abc'
    c = MainConfig(image); c.rx(hook, body + bytes([sum(body) & 0xff]))
    assert c.dispatched == [0x20E100] and c.notifies and c.notifies[0][2] == b'\x0c\x03\x00abc'
    checks += 1
    return checks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dir', type=Path, default=Path(__file__).resolve().parent / 'artifacts/screensaver-v15')
    args = ap.parse_args()
    manifest = json.loads((args.dir / 'manifest.json').read_text())
    total = 0
    for key in ('screen', 'screen_norearm'):
        entry = manifest[key]
        img = (args.dir / entry['file']).read_bytes()
        assert hashlib.sha256(img).hexdigest() == entry['candidate_sha256'] and len(img) == 180608
        n = verify_screen(img, entry['symbols'], entry['variant'])
        print(f'PASS screen[{entry["variant"]}]: {n} groups (real dispatcher + page table stock, framebuffer format vs the '
              f'REAL set_px_cb/0x19da/0x1858, SHOW blit, BEGIN/DATA/COMMIT slot 0, malformed, REG/alias/STATUS readback, sleep hook, '
              f'activity gating, slots 3/5 + presence map, SHOW <slot>, random/sequential picker, THRESHOLD/MODE, CLEAR, dead slot, '
              f'v14 forensics: verified unlock / erase blank-check / dropped-frame diag + resend / retention diag / err_addr+code, '
              f'v15: counter lever vs the REAL 0x1858, blit arms the counter, dispatch hook (pre-wait / r4 preserved / restore forced '
              f'FULL + sync bypass + policy recorded), post-restore grace, restore arming + wake_snap, pre-wait on every sleep path); '
              f'size == stock 180608; every flash op inside the addressed slot')
        total += n
    main_img = (args.dir / 'main-screensaver-v2-OPTIONAL-DO-NOT-FLASH.bin').read_bytes()
    assert hashlib.sha256(main_img).hexdigest() == manifest['main']['candidate_sha256']
    print(f'PASS main v2 (optional): {verify_main(main_img, manifest["main"]["symbols"])} groups')
    print('Offline emulation only: no boot, display, radio or real flash write executed.')


if __name__ == '__main__':
    main()
