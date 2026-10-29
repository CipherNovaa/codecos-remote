#!/usr/bin/env python3
"""Build the OFA e-paper multi-slot image screensaver firmware (Design A, v15).

OFFLINE ONLY.  Nothing is flashed or transmitted.  Two candidates are produced:

  * SCREEN (RT150T, Cortex-M0+): the installed mgs-font image plus an in-image
    extension (v15: six flash image slots 0x2e000 + i*0x2000, random/sequential
    slot picker, direct-framebuffer sleep blit, a55a command 0x50 receiver with
    BEGIN/DATA/COMMIT/SHOW/THRESHOLD/CLEAR/MODE/STATUS/REG and 100 readback
    registers, sleep/activity/LPTIM/page-dispatch hooks; v14 = verified SLOCK
    unlock, erase blank-check, received-vs-flash crc diagnostics and a re-sendable
    COMMIT; v15 = wake/restore hardening: every saver blit arms the stock partial
    counter so the page that replaces it is a FULL update, the page dispatch pre-waits
    a refresh in flight and forces the restore's flush full, and a 20 s post-restore
    grace keeps the restored page on the panel).  Five byte-accounted patch sites, all
    `bl` or dispatch-table halfwords; the whole extension sits in three zero-padding
    runs so the image stays exactly 180608 B.
  * MAIN (RTL8762C, Cortex-M4, OPTIONAL): the installed spare-keys-v2 image plus
    the additive W 0C forwarder (v2: sub-ops 0x08/0x09 accepted) and a reply relay
    spliced in front of the stock screen-response dispatcher.  The installed main
    v1 remains sufficient for the screen candidate: readback then goes through the
    cmd-02 masquerade (`W 0C 04 ii ff` + `W 02`).

Both extensions are assembled with clang/lld from the checked-in .S/.c/.ld, then
self-checked with capstone (including an ARMv6-M legality gate for the screen: the
v4 candidate patched 0x1848 with a Thumb-2 `b.w`, which a Cortex-M0+ faults on).
The behavioural (unicorn) validation lives in check_screensaver.py; this builder
refuses to emit if the capstone self-check or the byte-allowlist fails.  It does
NOT flash.  See SCREENSAVER-BUILD.md and SCREENSAVER-DEBUG-STATE.md.
"""
import argparse
import hashlib
import json
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---- pinned inputs ----------------------------------------------------------
SCREEN_BASE_SHA = '03ea324239012d7c5bf3266af898a6efdbdf8d5ca1f5eac8c33eeb007fbe9b51'  # mgs-font v5 candidate
MAIN_BASE_SHA = '6daaf9d75b57a2521b63689c44c7dabd80c1f247f403e389f65ceebb7214d9fa'      # installed spare-keys-v2
SCREEN_STOCK_SHA = '64670a392082daa0d708eb1f5f5ff58227e7d01d60da480ed56b157f5a60980d'   # v013f (recovery reference)

SCREEN_BASE = ROOT / 'artifacts/screen-font-v5-22px-condensed/candidate-screen-font-DO-NOT-FLASH.bin'
MAIN_BASE = ROOT / 'artifacts/spare-keys-v2/candidate-0.5.7-spare-keys-v2.bin'

# ---- geometry ---------------------------------------------------------------
SCREEN_VA = 0x1000          # file offset + SCREEN_VA == screen VA
MAIN_VA = 0x80DE00          # file offset + MAIN_VA == main VA

# screen patch sites (VA) and the stock bytes they replace
SCR_PAGE_TABLE_0x59 = 0x1edf8       # u32 page-builder table word for state 0x59
SCR_PAGE_TABLE_BLANK = 0x1ee65      # stock value at state 0x59 (blank builder | thumb)
SCR_DISPATCH_0x50 = 0x1c3a2         # u16 a55a dispatch entry for cmd 0x50: 0x1c304 + 2*(cmd-1).  v1..v5 patched
                                    # 0x1c3a4 (= cmd 0x51), so the screen NAKed every cmd 0x50 -- the whole saga.
SCR_DISPATCH_BASE = 0x1c306         # `add pc,r0` base; target = BASE + (halfword<<1)
SCR_ERROR_STUB = 0x1c456            # stock dispatch target for unused commands
SCR_SLEEP_CALL = 0xe632             # `bl 0xbe8c` inside the idle->sleep decision 0xe5a8
SCR_SLEEP_CALL_STOCK = bytes.fromhex('fdf72bfc')
SCR_SLEEP_STOCK_FN = 0xbe8c
SCR_BL_ACTIVITY = 0xbe12            # `ldr r0,[pc,#0x108]; movs r4,#0` after 0xbe10 prologue
SCR_BL_ACTIVITY_STOCK = bytes.fromhex('42480024')
SCR_BATT_SITE = 0x1ac68             # (v5 battery hook site; NOT patched in v6, asserted stock)
SCR_LPTIM_CALL = 0x1d67c            # `bl 0xbeec` in the mode-1 loop (LPTIM wake -> re-sleep consumer)
SCR_LPTIM_CALL_STOCK = bytes.fromhex('eef736fc')
SCR_BATT_SITE_STOCK = bytes.fromhex('64214143')
SCR_FN_1848 = 0x1848                # v4 patched this (illegally); v5 asserts it is stock
SCR_FN_1848_STOCK = bytes.fromhex('80b50020')
SCR_DISPATCH_CALL = 0xe694          # v15: `bl 0x1ec5c` inside the page redraw 0xe65c (shown := target; dispatch(target))
SCR_DISPATCH_CALL_STOCK = bytes.fromhex('10f0e2fa')
SCR_PAGE_DISPATCHER = 0x1ec5c
# v15: the stock refresh-policy plumbing the wake hardening relies on (asserted stock)
SCR_STOCK_POLICY_PATH = {
    0xe690: bytes.fromhex('28682060'),          # 0xe65c: shown := target, just before the dispatch call
    0xe698: bytes.fromhex('0548'),              # ... and after it (r0 reloaded: the dispatcher's return value is unused)
    0x1ec7e: bytes.fromhex('ac4d01202870'),     # dispatcher: 0x200008b8 := 1 before the page build
    0x1ee5e: bytes.fromhex('06462f70'),         # the state-0 page group clears 0x200008b8 (synchronous-refresh pages)
    0x1ef0a: bytes.fromhex('f0f797fd002806d0'), # dispatcher tail: FULL_FLAG from the policy 0xfa3c(state)
    0x186e: bytes.fromhex('0c4c20781e2804d3'),  # 0x1858: partial counter >= 0x1e -> full update
    0x1888: bytes.fromhex('0648e649085c06490978014300d010bd'),   # 0x1858 tail: settings[0x3b5] | 0x200008b8 -> skip the stock wait
    0x18a0: bytes.fromhex('240a0020'),          # literal pool: the partial counter 0x20000a24
    0x4da4: bytes.fromhex('250a0020'),          # literal pool: FULL_FLAG 0x20000a25
    0xc030: bytes.fromhex('b0b5f5f7c7fbfaf7'),  # full update: temperature load 0x17c4 + full LUT 0x6720 ...
    0xc090: bytes.fromhex('80b5faf717fb2220'),  # partial update: partial LUT 0x66c4 + 0x22 ...
}
# v12 direct blit: the stock EPD path it calls (first bytes of each routine, asserted stock)
SCR_FB = 0x20004c3c                 # EPD framebuffer, 21 B x 384 rows = 8064 B, 1 = white, MSB-first (set_px_cb 0xbff0)
SCR_FB_BYTES = 8064
SCR_STOCK_EPD_PATH = {
    0xbff0: bytes.fromhex('b0b507209843'),     # set_px_cb: bit = 1 << (7 - (x & 7)) ...
    0xc016: bytes.fromhex('05490a5c2243'),     # ... bright pixel ORs the bit (1 = white)
    0xc020: bytes.fromhex('a8b202490a5ca243'), # ... dark pixel clears it
    0x19da: bytes.fromhex('b0b504461cf0bffb2420'),  # RAM write: wait, cmd 0x24, 0x3f<<7 = 8064 data bytes
    0x1858: bytes.fromhex('10b503f09bfa0128'),  # refresh: full (0xc030) when 0x20000a25 == 1
    0x6598: bytes.fromhex('10b506200321'),     # flush prologue: NVIC IRQ6 off + PA10 -> GPIO
    0xa1fe: bytes.fromhex('80b50a460128'),     # PB0 write wrapper
    0x17a8: bytes.fromhex('b0b500240225'),     # touch INT (PA2) mask + flag clear
    0x5cfc: bytes.fromhex('b0b500240a25'),     # flush epilogue: PA10 -> UART
    0x17fc: bytes.fromhex('70b515460e46'),     # NVIC (irq, prio, enable)
    0xea10: bytes.fromhex('80b540200321'),     # BUSY reader: PB3
}
SCR_APPEND_VA = 0x2d180             # == image end; the bootloader REJECTS images larger than this (proved on hardware)
SCR_SLOT0, SCR_SLOT_SIZE, SCR_NSLOTS = 0x2e000, 0x2000, 6   # v13 image slots 0x2e000..0x3a000 (slot 0 proven writable on hardware)
SCR_FLASH_END = 0x40000             # stock erase/program bound (0x5034 / 0x50c0 check addr >> 18 == 0): a 256 KiB part
SCR_REGIONS = {                     # free zero padding after the mgs glyph bitmaps (glyph bitmaps end exactly at each start)
    '.text': (0x25e1c, 0x263ac),    # region A, 1424 B: command handler + flash helpers (3rd face, cap end file 0x253ac)
    '.b':    (0x25290, 0x256c0),    # region B, 1072 B: entry glue + hooks (1st face, cap end file 0x246c1)
    '.c':    (0x25898, 0x25c28),    # region C,  912 B: readback (2nd face: bitmaps end file 0x24897, cap end 0x24c2b)
}

# main patch sites
MAIN_W_TABLE = 0xA9E4               # file offset of the W-opcode tbh table (VA 0x8187e4)
MAIN_TBH_BASE = 0x8187E4
MAIN_W0C_ENTRY = MAIN_W_TABLE + 0x0C * 2
MAIN_FREE_MARKER = 0x0482           # tbh value for an unused opcode -> default handler 0x8190e8
MAIN_BRIDGE_VA = 0x82792c           # == image end; appended bridge entry
MAIN_RX_SITE = 0x81b938             # `b.w 0x81aaf4` tail call into the screen-response dispatcher
MAIN_RX_SITE_STOCK = bytes.fromhex('fff7dcb8')
MAIN_RX_DISPATCH = 0x81aaf4
MP_SIZE = 0x200
IMAGE_HEADER_SIZE = 0x400
SHA_OFFSET = MP_SIZE + 372

CLANG_COMMON = ['--target=arm-none-eabi', '-mthumb', '-fuse-ld=lld', '-nostdlib', '-Oz',
                '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                '-fno-asynchronous-unwind-tables']

# ARMv6-M: the only 32-bit encodings are BL, DMB/DSB/ISB, MRS/MSR (and UDF.W).
ARMV6M_WIDE_OK = {'bl', 'dmb', 'dsb', 'isb', 'mrs', 'msr', 'udf'}


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def realtek_digest(data):
    image = data[MP_SIZE:]
    return hashlib.sha256(image[12:372] + image[404:752] + image[1008:]).digest()


def _thumb_branch(site, target, lo_base):
    """Encode a Thumb-2 wide branch (BL if lo_base=0xD000, B.W if 0x9000)."""
    if (target - (site + 4)) & 1:
        raise ValueError('branch target not halfword aligned')
    val = (target - (site + 4)) >> 1
    if not -(1 << 23) <= val < (1 << 23):
        raise ValueError('branch target out of range')
    s = (val >> 23) & 1
    imm10 = (val >> 11) & 0x3FF
    imm11 = val & 0x7FF
    j1 = ((val >> 22) & 1 ^ 1) ^ s
    j2 = ((val >> 21) & 1 ^ 1) ^ s
    hi = 0xF000 | (s << 10) | imm10
    lo = lo_base | (j1 << 13) | (j2 << 11) | imm11
    return struct.pack('<HH', hi, lo)


def thumb_bl(site, target):
    return _thumb_branch(site, target, 0xD000)


def thumb_bw_m4(site, target):
    """B.W is Thumb-2 only (Cortex-M4 main).  Never use on the Cortex-M0+ screen."""
    return _thumb_branch(site, target, 0x9000)


def assemble(cpu, ld, entry, srcs, defines=(), sections=('.text',)):
    """clang/lld assemble one extension; return (code_bytes or {section: bytes}, {sym: va})."""
    with tempfile.TemporaryDirectory() as d:
        elf = Path(d) / 'p.elf'
        subprocess.run(['clang', *CLANG_COMMON, *defines, f'-mcpu={cpu}',
                        f'-Wl,-T,{ROOT / ld}', f'-Wl,--entry={entry}',
                        *[str(ROOT / s) for s in srcs], '-o', str(elf)], check=True)
        nm = subprocess.run(['llvm-nm', '--defined-only', str(elf)],
                            check=True, capture_output=True, text=True).stdout
        syms = {}
        for line in nm.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1] in 'TtAa':
                syms[parts[2]] = int(parts[0], 16)
        blobs = {}
        for sec in sections:
            binp = Path(d) / f'p{sec}.bin'
            subprocess.run(['llvm-objcopy', '-O', 'binary', f'--only-section={sec}',
                            str(elf), str(binp)], check=True)
            blobs[sec] = binp.read_bytes()
        # objdump honours the $t/$d mapping symbols, so literal pools and tables are
        # printed as data, not mis-decoded as instructions (see armv6m_listing_ok)
        listing = subprocess.run(['llvm-objdump', '-d', str(elf)],
                                 check=True, capture_output=True, text=True).stdout
        LISTINGS[entry] = listing
        return (blobs['.text'] if sections == ('.text',) else blobs), syms


LISTINGS = {}


def assemble_stable(cpu, ld, entry, srcs, defines=(), sections=('.text',)):
    a, syms = assemble(cpu, ld, entry, srcs, defines, sections)
    b, _ = assemble(cpu, ld, entry, srcs, defines, sections)
    if a != b:
        raise ValueError('clang output is not reproducible; re-audit before trusting the pin')
    return a, syms


def _md():
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
    return Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)


def capstone_ok(code, base, entry_syms, armv6m=False):
    """Every entry path disassembles to real instructions; optionally ARMv6-M legal."""
    md = _md()
    seen = {i.address: i for i in md.disasm(code, base)}
    for name, va in entry_syms.items():
        off = va - base
        while off < len(code):
            insns = list(md.disasm(code[off:off + 4], base + off, count=1))
            if not insns:
                raise ValueError(f'{name}: undecodable bytes at {base + off:#x}')
            ins = insns[0]
            if armv6m and ins.size == 4 and ins.mnemonic not in ARMV6M_WIDE_OK:
                raise ValueError(f'{name}: {ins.mnemonic} at {ins.address:#x} is not ARMv6-M')
            off += ins.size
            if ins.mnemonic in ('bx', 'pop') or (ins.mnemonic == 'b' and 'lr' in ins.op_str):
                break
            if off - (va - base) > 512:
                break
    return len(seen)


def armv6m_listing_ok(listing):
    """Refuse any 32-bit instruction a Cortex-M0+ cannot execute, using the
    assembler's own code/data mapping (llvm-objdump prints pools as .word/.short).
    Returns the number of instructions inspected."""
    import re
    n = 0
    for line in listing.splitlines():
        m = re.match(r'\s*([0-9a-f]+):\s+((?:[0-9a-f]{4}\s)+)\s*(\S+)', line)
        if not m:
            continue
        halfwords = m.group(2).split()
        mnem = m.group(3)
        n += 1
        if mnem.startswith('.'):
            continue                            # data
        if len(halfwords) == 2 and mnem.split('.')[0] not in ARMV6M_WIDE_OK:
            raise ValueError(f'ARMv6-M violation: {line.strip()}')
    if n == 0:
        raise ValueError('empty listing')
    return n


def assert_bl(site, data):
    hi, lo = struct.unpack('<HH', data)
    if (hi & 0xF800) != 0xF000 or (lo & 0xD000) != 0xD000:
        raise ValueError(f'patch at {site:#x} is not a BL (ARMv6-M has no B.W)')


# ---------------------------------------------------------------------------- screen
def build_screen(base, blobs, syms):
    if sha256(base) != SCREEN_BASE_SHA:
        raise ValueError('screen base is not the pinned mgs-font candidate')
    if len(base) != SCR_APPEND_VA - SCREEN_VA:
        raise ValueError('screen base does not end at the expected VA')

    ss_cmd = syms['ss_cmd_stub']
    ss_sleep = syms['ss_sleep_hook']
    ss_act = syms['ss_activity_hook']
    if ss_cmd != SCR_REGIONS['.b'][0]:
        raise ValueError('ss_cmd_stub must be first in region B')
    if SCR_SLOT0 < SCR_APPEND_VA or SCR_SLOT0 + SCR_NSLOTS * SCR_SLOT_SIZE > SCR_FLASH_END:
        raise ValueError('image slots must lie past the app image and inside the 256 KiB flash bound')

    out = bytearray(base)
    allowed = set()
    for sec, (lo, hi) in SCR_REGIONS.items():
        code = blobs[sec]
        if lo + len(code) > hi:
            raise ValueError(f'{sec} is {len(code)} B; only {hi - lo} B free at {lo:#x}')
        region = slice(lo - SCREEN_VA, lo - SCREEN_VA + len(code))
        if any(base[region]):
            raise ValueError(f'{sec}: in-image region is not all-zero in the base (would overwrite data)')
        out[region] = code
        allowed.update(range(region.start, region.stop))

    def stock(va, want):
        got = bytes(base[va - SCREEN_VA:va - SCREEN_VA + len(want)])
        if got != want:
            raise ValueError(f'stock bytes at {va:#x} are {got.hex()}, expected {want.hex()}')

    def patch(va, data):
        off = va - SCREEN_VA
        out[off:off + len(data)] = data
        allowed.update(range(off, off + len(data)))

    # v12: no saver page any more -- the page table word for state 0x59 stays STOCK
    stock(SCR_PAGE_TABLE_0x59, struct.pack('<I', SCR_PAGE_TABLE_BLANK))

    cur = struct.unpack_from('<H', base, SCR_DISPATCH_0x50 - SCREEN_VA)[0]
    if SCR_DISPATCH_BASE + (cur << 1) != SCR_ERROR_STUB:
        raise ValueError('command 0x50 is not the stock error stub')
    hw = (ss_cmd - SCR_DISPATCH_BASE) >> 1
    if not 0 <= hw <= 0xFFFF or SCR_DISPATCH_BASE + (hw << 1) != ss_cmd:
        raise ValueError('command 0x50 handler out of dispatch range')
    patch(SCR_DISPATCH_0x50, struct.pack('<H', hw))

    stock(SCR_SLEEP_CALL, SCR_SLEEP_CALL_STOCK)
    bl = thumb_bl(SCR_SLEEP_CALL, ss_sleep); assert_bl(SCR_SLEEP_CALL, bl); patch(SCR_SLEEP_CALL, bl)
    stock(SCR_BL_ACTIVITY, SCR_BL_ACTIVITY_STOCK)
    bl = thumb_bl(SCR_BL_ACTIVITY, ss_act); assert_bl(SCR_BL_ACTIVITY, bl); patch(SCR_BL_ACTIVITY, bl)
    stock(SCR_LPTIM_CALL, SCR_LPTIM_CALL_STOCK)
    bl = thumb_bl(SCR_LPTIM_CALL, syms['ss_lptim_hook']); assert_bl(SCR_LPTIM_CALL, bl); patch(SCR_LPTIM_CALL, bl)
    stock(SCR_DISPATCH_CALL, SCR_DISPATCH_CALL_STOCK)  # v15: the stock bl must target the page dispatcher
    if struct.unpack('<HH', SCR_DISPATCH_CALL_STOCK) != struct.unpack('<HH', thumb_bl(SCR_DISPATCH_CALL, SCR_PAGE_DISPATCHER)):
        raise ValueError('0xe694 is not `bl 0x1ec5c`')
    bl = thumb_bl(SCR_DISPATCH_CALL, syms['ss_dispatch_hook']); assert_bl(SCR_DISPATCH_CALL, bl); patch(SCR_DISPATCH_CALL, bl)
    stock(SCR_FN_1848, SCR_FN_1848_STOCK)
    stock(SCR_BATT_SITE, SCR_BATT_SITE_STOCK)
    for va, want in SCR_STOCK_EPD_PATH.items():        # the stock EPD path our blit calls into must be untouched
        stock(va, want)
    for va, want in SCR_STOCK_POLICY_PATH.items():     # v15: the refresh-policy plumbing the hardening leans on
        stock(va, want)

    if len(out) != len(base):
        raise ValueError('image size changed; the bootloader only accepts the stock size')
    changed = {i for i, (a, b) in enumerate(zip(base, out)) if a != b}
    if changed - allowed:
        raise ValueError('screen change outside the allowlist')
    for lo, hi in ((0x1c2ce, 0x1c2f2), (0x1c5e0, 0x1c664)):
        if bytes(out[lo - SCREEN_VA:hi - SCREEN_VA]) != bytes(base[lo - SCREEN_VA:hi - SCREEN_VA]):
            raise ValueError('recovery/OTA path changed')

    syms_out = {k: syms.get(k) for k in ('ss_cmd_stub', 'ss_sleep_hook', 'ss_activity_hook',
                                         'ss_lptim_hook', 'ss_dispatch_hook', 'ss_command', 'ss_sleep_draw', 'ss_push', 'fb_matches',
                                         'wait_busy', 'pre_wait', 'ss_activity', 'ss_lptim', 'ss_dispatch', 'reg_read', 'masquerade', 'reg_reply',
                                         'pick', 'slot_map', 'present', 'fl_erase', 'fl_write', 'fl_unlock', 'ss_init')}
    return bytes(out), sorted(changed), syms_out


# ---------------------------------------------------------------------------- main
def build_main(base, code, syms):
    if sha256(base) != MAIN_BASE_SHA:
        raise ValueError('main base is not the installed spare-keys-v2 image')
    if MAIN_VA + len(base) != MAIN_BRIDGE_VA:
        raise ValueError('main base does not end at the expected bridge VA')
    if syms['ss_bridge'] != MAIN_BRIDGE_VA:
        raise ValueError('ss_bridge must be first at the append VA')
    if struct.unpack_from('<H', base, MAIN_W0C_ENTRY)[0] != MAIN_FREE_MARKER:
        raise ValueError('W 0C is not an unused opcode in this image')

    out = bytearray(base)
    allowed = set(range(len(base), len(base) + len(code)))
    out.extend(code)
    out.extend(bytes((-len(code)) % 16))
    allowed.update(range(len(base) + len(code), len(out)))

    sector_end = (MAIN_VA + len(base) + 0xFFF) & ~0xFFF
    if MAIN_VA + len(out) > sector_end:
        raise ValueError('main extension overflows the final APP sector')

    delta = MAIN_BRIDGE_VA - MAIN_TBH_BASE
    if delta % 2 or not 0 <= delta // 2 <= 0xFFFF:
        raise ValueError('W 0C dispatch branch out of range')
    struct.pack_into('<H', out, MAIN_W0C_ENTRY, delta // 2)
    allowed.update(range(MAIN_W0C_ENTRY, MAIN_W0C_ENTRY + 2))

    # v2: splice the reply relay in front of the stock response dispatcher
    off = MAIN_RX_SITE - MAIN_VA
    if bytes(base[off:off + 4]) != MAIN_RX_SITE_STOCK:
        raise ValueError('response-dispatcher tail call is not the stock b.w 0x81aaf4')
    bw = thumb_bw_m4(MAIN_RX_SITE, syms['ss_rx_hook'])
    out[off:off + 4] = bw
    allowed.update(range(off, off + 4))

    struct.pack_into('<I', out, MP_SIZE + 8, len(out) - MP_SIZE - IMAGE_HEADER_SIZE)
    allowed.update(range(MP_SIZE + 8, MP_SIZE + 12))
    out[SHA_OFFSET:SHA_OFFSET + 32] = realtek_digest(out)
    allowed.update(range(SHA_OFFSET, SHA_OFFSET + 32))

    changed = {i for i, (a, b) in enumerate(zip(base, out)) if a != b}
    if changed - allowed:
        raise ValueError('main change outside the allowlist')
    if bytes(out[len(base):len(base) + len(code)]) != code:
        raise ValueError('main appended code mismatch')
    return bytes(out), sorted(changed), {k: syms[k] for k in ('ss_bridge', 'ss_command', 'ss_rx_hook', 'ss_rx_relay')}


def reproduce_main(stock):
    """Rebuild the screensaver main candidate (v2) from the pinned stock asset.

    Used by flash.py's acceptance gate: it reproduces spare-keys-v2, assembles the
    W 0C forwarder + relay from source, and returns the exact candidate bytes so the
    flasher can refuse anything that is not byte-identical to a from-source rebuild.
    """
    from spare_keys_firmware import build as build_spare_keys
    base, _ = build_spare_keys(stock, 2)          # installed spare-keys-v2
    code, syms = assemble_stable('cortex-m4', 'screensaver_main.ld', 'ss_bridge',
                                 ['screensaver_main.S', 'screensaver_main.c'])
    image, _, _ = build_main(base, code, syms)
    return image


def load_base(path, want_sha, label):
    data = path.read_bytes()
    if sha256(data) != want_sha:
        raise ValueError(f'{label} base SHA mismatch at {path}')
    return data


def blocks(offsets):
    out = []
    for o in offsets:
        if out and o == out[-1][1] + 1:
            out[-1][1] = o
        else:
            out.append([o, o])
    return [{'from': hex(a), 'to': hex(b), 'bytes': b - a + 1} for a, b in out]


SCREEN_VARIANTS = {'rearm': ('-DSS_REARM=1',), 'norearm': ('-DSS_REARM=0',)}


def build_screen_variant(name):
    blobs, syms = assemble_stable('cortex-m0plus', 'screensaver_screen.ld', 'ss_cmd_stub',
                                  ['screensaver_screen.S', 'screensaver_screen.c'],
                                  SCREEN_VARIANTS[name], ('.text', '.b', '.c'))
    entries = {k: syms[k] for k in ('ss_cmd_stub', 'ss_sleep_hook', 'ss_activity_hook', 'ss_lptim_hook', 'ss_dispatch_hook')}
    capstone_ok(blobs['.b'], SCR_REGIONS['.b'][0], entries, armv6m=True)
    armv6m_listing_ok(LISTINGS['ss_cmd_stub'])          # every section, via the assembler's code/data mapping
    base = load_base(SCREEN_BASE, SCREEN_BASE_SHA, 'screen')
    img, changed, symmap = build_screen(base, blobs, syms)
    return blobs, img, changed, symmap


def build_all():
    scr = {name: build_screen_variant(name) for name in SCREEN_VARIANTS}
    main_code, main_syms = assemble_stable('cortex-m4', 'screensaver_main.ld',
                                           'ss_bridge', ['screensaver_main.S', 'screensaver_main.c'])
    capstone_ok(main_code, main_syms['ss_bridge'], {k: main_syms[k] for k in ('ss_bridge', 'ss_command', 'ss_rx_hook')})
    main_base = load_base(MAIN_BASE, MAIN_BASE_SHA, 'main')
    main_img, main_changed, main_symmap = build_main(main_base, main_code, main_syms)
    return scr, (main_code, main_img, main_changed, main_symmap)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', type=Path, default=ROOT / 'artifacts/screensaver-v15')
    ap.add_argument('--force', action='store_true', help='overwrite an existing output dir')
    args = ap.parse_args()

    scr, (main_code, main_img, main_changed, main_symmap) = build_all()
    outdir = args.output_dir
    outdir.mkdir(parents=True, exist_ok=args.force)
    files = {'rearm': 'screen-screensaver-v15-REARM-DO-NOT-FLASH.bin',
             'norearm': 'screen-screensaver-v15-NOREARM-DO-NOT-FLASH.bin'}
    for name, (blobs, img, changed, symmap) in scr.items():
        (outdir / files[name]).write_bytes(img)
    main_out = outdir / 'main-screensaver-v2-OPTIONAL-DO-NOT-FLASH.bin'
    main_out.write_bytes(main_img)

    def screen_entry(name):
        blobs, img, changed, symmap = scr[name]
        return {
            'file': files[name],
            'variant': name,
            'candidate_sha256': sha256(img), 'candidate_bytes': len(img), 'size_equals_stock': len(img) == 180608,
            'regions': {sec: {'va': hex(SCR_REGIONS[sec][0]), 'bytes': len(blobs[sec]),
                              'free': SCR_REGIONS[sec][1] - SCR_REGIONS[sec][0] - len(blobs[sec])} for sec in blobs},
            'symbols': {k: hex(v) for k, v in symmap.items() if v is not None},
            'changed_byte_count': len(changed), 'changed_blocks': blocks(changed),
        }

    manifest = {
        'status': 'OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED',
        'version': 15,
        'recommended_file': files['norearm'],
        'v15_wake_hardening': {
            'bug_1_keypress_freeze': 'v14 restored by SHOWN := 0xff only; the stock page dispatcher (0x1ec5c, tail 0x1ef0a) then chose the '
                                     'refresh mode from its policy 0xfa3c(state): PARTIAL (0x22/0xcf with the short LUT 0x27f36, two active '
                                     'phases) for the device pages 0x5b..0x5d (mask 0x7803) and for any page after its first draw '
                                     '(0x20000a2d).  A partial LUT cannot erase a dense full-contrast saver, so the panel kept the saver '
                                     'while the framebuffer already held the page; every later wake was `ignored` (saver_active 0) and '
                                     'nothing re-rendered until the next sleep-draw\'s forced FULL blit.',
            'bug_2_pickup_double_switch': 'same lost restore; when main then went quiet the sleep hook found fb != saver, picked ANOTHER '
                                          'slot (the current one is excluded) and drew it with a full refresh -- the visible "switch" -- '
                                          'and the following wake restored the page.  A quiet pickup would also re-sleep 500 ms after the '
                                          'wake stamp and swap images even with a working restore.',
            'fix_1_counter_lever': 'ss_push (sleep-draw and SHOW) leaves the stock partial counter 0x20000a24 at 0x1e: 0x1858 (0x186e) '
                                   'then does ONE full update 0xc030 and zeroes it, whatever FULL_FLAG the dispatcher chose; the stock '
                                   'policy resumes afterwards (no sticky full-refresh flag).',
            'fix_2_dispatch_hook': 'new patch site 0xe694 (`bl 0x1ec5c` -> ss_dispatch_hook): before EVERY page build, if BUSY reads high '
                                   'wait <= 4 s (never the stock 0x1e160 whose 1.5 s timeout pulses RST mid-refresh); after a genuine '
                                   'restore re-assert the counter, set 0x200008b8 := 1 (the restore\'s full refresh is guarded by our bounded '
                                   'waits, not the RST-on-timeout wait) and record rst_policy (the FULL_FLAG the stock chose) and rst_sync '
                                   '(the 0x200008b8 it left) -- the two bytes that settle the root cause on hardware.',
            'fix_3_grace': 'for 20 s of awake time after a genuine restore the sleep hook returns 0 (stock sleep deferred, decided again '
                           'next pass) instead of drawing a NEW image, so a quiet pickup/keypress keeps the restored page on the panel; '
                           'quiet_ms10 records how long main kept the screen awake after the wake; grace_defers counts the deferrals.',
            'fix_4_prewaits': 'the sleep hook pre-waits BUSY on every path (draw, skip, saver-off) and SHOW pre-waits too: the panel is '
                              'never deep-slept or overwritten mid-refresh.',
            'kept': 'v14 sleep-draw (direct blit, wrapper order, BUSY poll/fallback, scrub, NOREARM), picker, slots, forensics, '
                    'readback numbering 0..83 unchanged; reg_read is table-driven (REG_SRC/REG_LIVE) to fit region C.',
        },
        'flash_path': 'ONLY the OFA app commits an e-paper image: Advanced Mode -> Custom OTA -> "E-paper - Select File" '
                      '(push the .bin to the tablet).  screen_direct.py transfers but never issues the finalize.  '
                      'Size must stay 180608 B.  Flash the NOREARM file (the v12 NOREARM build is the one proven on hardware; '
                      'stock touch does not wake from sleep, so the re-arm buys nothing and re-opens the phantom-touch door).',
        'v13_features': {
            'slots': f'{SCR_NSLOTS} x {SCR_SLOT_SIZE:#x} B at {SCR_SLOT0:#x} + i*{SCR_SLOT_SIZE:#x} (i = 0..{SCR_NSLOTS - 1}); '
                     'BEGIN <slot> erases exactly that slot (16 sectors), DATA/COMMIT program only it (fl_ok range-checks every '
                     'program to the BEGIN slot; fl_erase range-checks to the slot window); the header SSAV+8072 is written only '
                     'after the crc32 of the programmed bytes verified, so a slot that fails to erase/program never becomes present.',
            'presence': 'register 0 = 6-bit slot map (bit i = slot i holds SSAV + len 8072); STATUS byte 2 likewise.',
            'picker': 'at the 0xe632 sleep decision, among the FILLED slots: MODE 0 random (default) = LCG '
                      'x = (x ^ ms_tick ^ ADC_raw << 20) * 1103515245 + 12345, index = ((x >> 16) * count) >> 16 (no divide), '
                      'the slot already on the panel is excluded whenever another one is filled; MODE 1 sequential = the next '
                      'filled slot after the current one.  No filled slot -> stock sleep, nothing drawn.',
            'skip_rule': 'unchanged from v12: a sleep entry whose framebuffer already holds the CURRENT slot sleeps silently '
                         '(skips++), so the image only changes after a genuine wake re-dispatched the normal page.',
            'clear': 'CLEAR <slot|0xff> erases the header sector (512 B) of a present slot / of every present slot; absent '
                     'slots are not touched.',
            'threshold': 'THRESHOLD 0 disables the saver (stock sleep), any other value enables it; the value is kept for '
                         'readback only -- the draw is always at the sleep instant (v12 cadence).  0xFFii = REG alias, '
                         '0xFExx reserved (CHEM alias, NAK in this build).',
            'mode': 'MODE 0|1 (a third byte, as main v1 forwards it, is ignored); MODE 2 (fixed slot) NAKs.',
            'show': 'SHOW = blit the current slot (or a pick) while awake; SHOW <slot> = blit that slot (NAK if absent).',
            'entropy': 'ms tick 0x5150 (SysTick; how long the user was active before the idle) ^ battery ADC raw 0x20000a70, '
                       'folded into a persistent LCG in RAM (survives deep sleep; a reset reseeds from the tick).',
            'cut': 'nothing -- all of BEGIN-per-slot, presence map, random, sequential, THRESHOLD on/off, CLEAR slot/all fit.',
            'ram': 'ss_state grew 40 -> 52 B in v13 and 52 -> 68 B in v14 (0x20007f80..0x20007fc4), magic SSRE; still above the Keil ZI+stack end 0x20007e58.',
        },
        'v14_forensics': {
            'why': 'v13 could not program slot 0 (0x2e000) on hardware while slot 1 (0x30000) worked; the flashed v13 bytes were traced '
                   'under a SLOCK/CR-faithful model of the REAL stock driver and are op-for-op identical to v12/v6 for slot 0 '
                   '(same 525 single-bit SLOCK2 writes, 16 erases, per-2KiB programs, base/crc/header for i = 0).  The v12 '
                   '"proof" was hollow: no `set` ever ran under v12, and every earlier slot-0 rewrite carried the SAME image, which '
                   'cannot distinguish a working erase/program from a silent no-op.  The live reg 12 after the slot-2 failure was 0 '
                   '(no erase/program/header error flagged) -- the COMMIT crc failed, i.e. bytes never landed or did not retain.',
            'unlock': 'fl_unlock = stock 0x1afbc + RM 10.3.1 Step 7: read the SLOCK bit back, retry <= 8, code 0xa if it never sets.',
            'erase': 'each erased sector is read back as 512 x 0xFF (RM 10.6.1: a protected sector erases WITHOUT effect) -> code 8.',
            'commit': 'diag = 0x10 | (crc of RECEIVED bytes == want) | (crc of FLASH == want) << 1; a wrong flash crc NAKs but keeps '
                      'rx_active = 1 so the tool re-sends DATA (identical bytes are a no-op, dropped frames fill their 0xFF cells) '
                      'and retries COMMIT without a new erase.',
            'regs': '61-83 (see readback.registers): err_addr/err_code name the failing address and driver code; FLASH_IFR/SLOCK '
                    'readbacks show the lock state the die actually holds.',
            'decisive_hw_tests': ['codec_screensaver reg 12 / reg 59 on the CURRENT v13 (RAM still holds the slot-0 verdict unless the '
                                  'batteries were pulled again)', 'set image-a.png --slot 0 and set image-c.png --slot 1 on v13: the runs so far '
                                  'confound ADDRESS (slot 1 only) with IMAGE (image-a only)'],
        },
        'design': 'v15 = v14 + wake/restore hardening (see v15_wake_hardening).  v14 = v13 + forensic flash path.  v13 = v12 with a DYNAMIC source slot.  v12 = DIRECT FRAMEBUFFER BLIT at the 0xe632 sleep decision, no LVGL, no saver page (state 0x59 table '
                  'word left stock).  The slot blob\'s 8064 pixel bytes (after its 8-byte palette) are copied INVERTED into '
                  'the EPD framebuffer 0x20004c3c, pushed with the stock RAM write 0x19da and refresh 0x1858 (full-refresh '
                  'flag 0x20000a25 forced and restored), wrapped exactly like the stock flush callback 0xbf80 (0x6598, PB0 := 1 '
                  'via 0xa1fe, 0x17a8 ... PB0 := 0, 0x5cfc, NVIC IRQ6 on), then an UNCONDITIONAL bounded BUSY poll (0xea10, '
                  '<= 4 s; 3 s fixed fallback if BUSY never reads high), 100 ms settle, [rearm build] 0x1848, 50 ms settle, '
                  'wake-flag + touch-event scrub, and the stock sleep entry 0xbe8c in the SAME pass.  v11 proved the LVGL '
                  'flush callback never runs at that instant (busy_first 0 / flushed 0) and v7..v10 that a page-0x59 state '
                  'gets "restored" by self-generated wakes; both are gone.  Slot 0 @ 0x2e000 (8 KiB).  Region A (.text, '
                  '0x25e1c) = command handler + flash; region B (.b, 0x25290) = entry glue, hooks, blit; region C (.c, '
                  '0x25898) = readback.',
        'framebuffer_format': {
            'finding': 'set_px_cb 0xbff0: index = 21*y + (x >> 3), bit = 1 << (7 - (x & 7)) (MSB-first, 21-byte stride, '
                       '384 rows = 8064 B, same as the blob); a pixel whose brightness (0x10948) is >= 0x81 ORs the bit, a '
                       'darker one clears it -> framebuffer 1 = WHITE.  The lv_img blob (screensaver_prep.pack_1bpp) stores '
                       '1 = palette[1] = BLACK ink.  Polarity is inverted, bit order and stride match.',
            'transform': 'fb[i] = ~blob[8 + i] for i in 0..8063 (bytewise NOT), bit-exact to what LVGL would paint for a '
                         'full-panel lv_img of that blob.  0x19da streams cmd 0x24 + the 8064 framebuffer bytes unchanged.',
            'verified_by': 'check_screensaver.py runs the REAL set_px_cb 0xbff0 over every pixel of a random image and the '
                           'REAL 0x19da/0x1858 with the SPI primitives hooked, and compares with the blit.',
        },
        'touch_int_finding': '0x1848 (v7..v11 called it the "backlight/power-down") is Gpio_EnableIrq(PA2, falling): the '
                             'FT6x36 touch controller INT (I2C 0x38, read by 0x4e3c on the PA2 event 0x20006e43).  The stock '
                             'flush masks it (0x17a8) during the SPI burst and the loop re-arms it 100 ms after every flush '
                             '(0x1d5d6).  Without the re-arm a touch cannot wake the remote while the saver sleeps (keys still '
                             'can: UART / PA10 RX edge).  Two builds: the default re-arms after the refresh has finished and '
                             'settled (stock behaviour restored); NOREARM never calls 0x1848 (touch INT stays masked until the '
                             'next awake loop pass re-arms it at 0xe7f0, since ARMED is cleared).',
        'sleep_draw': {
            'order': 'present? -> fb already == ~image ? (skip, sleep silently) -> pre-wait BUSY (<= 4 s) -> 0x6598 -> '
                     'PB0 := 1 -> 0x17a8 -> copy/invert 8064 B -> 0x19da -> FULL_FLAG := 1, 0x1858, restore -> PB0 := 0 -> '
                     '0x5cfc -> NVIC IRQ6 on -> 5 ms -> busy_first -> BUSY poll (<= 4 s) or 3 s fallback -> 100 ms -> '
                     '[rearm: 0x1848] -> 50 ms -> scrub 0x20006e50/51 (+ 0x20006e43, re-arm again if it fired) -> '
                     '[norearm: ARMED := 0] -> if main talked during the blit (rx count != 0) defer the sleep ONE pass -> '
                     'return 1 -> stock 0xbe8c (LPTIM arm, EPD deep sleep 0x10, WFI) in the same pass.',
            'activity': 'restore (SHOWN := 0xff so 0xe65c re-dispatches TARGET through ss_dispatch -> the page dispatcher; v15 '
                        'forces that flush to a FULL update and starts the 20 s grace) only for genuine input while the saver is '
                        'on the panel: a UART frame while asleep (0x1d48f), the touch INT (0x1d635), or the flags path (0x1d531) '
                        'with the UART RX-edge source or, for the PA8 line, once >= 1 LPTIM wake has passed since the draw.  '
                        'Everything else is counted.',
            'show': 'cmd 0x50 sub 03 = the same blit while awake (no waits after the refresh): an immediate on-panel proof of '
                    'the framebuffer format; the next page change overwrites it; the next sleep finds fb == ~image and skips.',
        },
        'readback': {
            'with_installed_main_v1': 'W 0C 04 ii ff (THRESHOLD alias = REG ii) then W 02: inventory[-2:] == (0x80|ii, value); '
                                      'W 0C 04 ff ff restores the real version bytes 01 3f.  codec_screensaver status does all of it.',
            'with_main_v2': 'W 0C 07 STATUS (16 B: S 7 + regs 0..13, relayed as notify 0c 03) and W 0C 08 ii REG',
            'registers': '0 slot_present,1 cmd_calls,2 sleep_hits,3 sleep_draws(sleep blits),4 show_blits,5 restores,6 act_calls,'
                         '7 act_mode,8 shown,9 target,10 lp_mode,11 rx_active,12 last_err(sub+1),13 inits,14 draw_target,'
                         '15 0x56 sentinel,16 ext_version 12,17 batt_pct,18 BL_DONE 0x20000871,19 FLUSH_DONE 0x20006e46,'
                         '20 ARMED 0x20006e44,21/22 mV,23 variant(3 rearm,4 norearm),24 act_caller(1 first-run,2 uart-in-sleep,'
                         '3 flags,4 touch-INT-in-sleep,5 other),25 restore_caller,26 ignored_caller,27 ignored,28 fb_match(LIVE),'
                         '29 ign_src,30 rst_src,31 skips,32 busy_ms10,33 busy_timeouts,34 saver_active,35 lptim_since_draw,'
                         '36 suppressed,37 lptim_wakes,38 scrubbed,39 lptim period s,40 busy_first,41 busy_pre,42 pre_ms10,'
                         '43 busy_fallbacks,44 pa2_scrubbed,45 rearms,46/47 fb bytes written (0x80 0x1f = 8064),'
                         '48 last_path(1 sleep blit,2 show blit,3 skip),49 PANEL_ABSENT 0x20006bc4,50 PA2_EVENT 0x20006e43,51 defers,'
                         '52 cur_slot(0xff none),53 mode(0 random,1 sequential),54/55 threshold lo/hi,56 picks,57 map_at_draw,'
                         '58 disabled,59 rx_slot,60 seed low byte,'
                         '61-64 rx_crc STATE (LE; crc = ~state),65/66 rx_frames,67/68 rx_bytes,69-72 err_addr,73 err_code'
                         '(1 readback mismatch,6 OP refused,7 erase driver,8 sector not blank after erase,9 BUSY timeout,0xa SLOCK bit never set,'
                         '0xfc init,0xfd/0xfe range),74 diag(0x10|rx_ok|flash_ok<<1 at the last COMMIT),75 slock_tries,76 pad,'
                         '77 FLASH_IFR[7:0] (bit1 IF1 alarm),78 FLASH_CR[7:0],79 SLOCK2[31:24],80/81/82 SLOCK3[7:0]/[15:8]/[23:16],83 first DATA byte in flash,'
                         'v15: 84 rst_pending,85 grace_armed,86 disp_calls,87 disp_forced_full,88 disp_busy_pre,89 disp_ms10 (x8 ms),90 disp_timeouts,'
                         '91 grace_defers,92 quiet_ms10 (awake ms/8 from the restore to the first sleep decision),93 wake_snap (bit0 rx-edge,'
                         '1 slept,2 lptim-woke,3 BUSY at wake,4 PA2 event,5 ARMED,7:6 pre-wake mode),94 rst_policy (FULL_FLAG the stock chose '
                         'at the restore dispatch: 0 = partial = the v14 freeze mode),95 rst_sync (0x200008b8 the stock left: 0 = its 1.5 s '
                         'RST-on-timeout wait would have guarded the restore),96 PARTIAL_CNT 0x20000a24 LIVE,97 FULL_FLAG 0x20000a25 LIVE,'
                         '98 SYNC_SKIP 0x200008b8 LIVE,99 grace seconds',
        },
        'toolchain': {'clang_flags': CLANG_COMMON, 'screen_cpu': 'cortex-m0plus', 'main_cpu': 'cortex-m4'},
        'screen': screen_entry('rearm'),
        'screen_norearm': screen_entry('norearm'),
        'screen_common': {
            'base_sha256': SCREEN_BASE_SHA,
            'patches': [
                {'site': hex(SCR_DISPATCH_0x50), 'what': 'a55a cmd 0x50 dispatch entry (0x1c304 + 2*(0x50-1)) -> ss_cmd_stub'},
                {'site': hex(SCR_SLEEP_CALL), 'what': 'bl 0xbe8c (idle->deep-sleep entry) -> bl ss_sleep_hook'},
                {'site': hex(SCR_BL_ACTIVITY), 'what': 'ldr/movs @0xbe12 -> bl ss_activity_hook (restore on genuine input only)'},
                {'site': hex(SCR_LPTIM_CALL), 'what': 'bl 0xbeec @0x1d67c (LPTIM wake consumer) -> bl ss_lptim_hook (window expiry clock)'},
                {'site': hex(SCR_DISPATCH_CALL), 'what': 'v15: bl 0x1ec5c @0xe694 (page redraw 0xe65c) -> bl ss_dispatch_hook (pre-wait BUSY; force the '
                                                         'restore flush FULL; record the stock policy)'},
            ],
            'not_patched': {hex(SCR_PAGE_TABLE_0x59): 'page table[0x59] stays the stock blank builder (no saver page in v12)'},
            'framebuffer': {'va': hex(SCR_FB), 'bytes': SCR_FB_BYTES},
            'placement': 'free zero padding after the mgs glyph bitmaps; every glyph bitmap ends exactly at the run starts '
                         '(file 0x2428d/0x24897/0x24e19); capacities end file 0x246c1/0x24c2b/0x253ac.',
            'armv6m_gate': 'every patched site is a BL; in-image text contains no non-ARMv6-M wide instruction',
            'recovery_path': 'command 0x40 special-case 0x1c2ce..0x1c2f2 and OTA idiom 0x1c5e0..0x1c664 asserted byte-identical; '
                             '0x46/0x47/0x48 handlers untouched; 0x1848, 0x1ac68 and the whole stock EPD path the blit calls '
                             '(0xbff0/0x19da/0x1858/0x6598/0xa1fe/0x17a8/0x5cfc/0x17fc/0xea10) asserted stock; v15 also asserts the '
                             'refresh-policy plumbing (0xe690/0xe698, 0x1ec7e, 0x1ee5e, 0x1ef0a, 0x186e, 0x1888, the 0x20000a24/0x20000a25 '
                             'pools, 0xc030/0xc090 heads)',
            'ram': 'ss_state @ 0x20007f80 (84 B) above the Keil ZI+stack region end 0x20007e58; magic SSRF',
            'slots': {'base': hex(SCR_SLOT0), 'size': hex(SCR_SLOT_SIZE), 'count': SCR_NSLOTS,
                      'end': hex(SCR_SLOT0 + SCR_NSLOTS * SCR_SLOT_SIZE),
                      'flash_banks': 'slot 0 = bank 2 (0x2xxxx, SLOCK reg 4 via 0x1afbc); slots 1..5 = bank 3 (0x3xxxx, SLOCK reg 5)'},
        },
        'main': {
            'optional': True, 'note': 'NOT required; the installed main v1 forwards W 0C and the masquerade readback works with it.',
            'base_sha256': MAIN_BASE_SHA, 'candidate_sha256': sha256(main_img), 'candidate_bytes': len(main_img),
            'symbols': {k: hex(v) for k, v in main_symmap.items()},
            'changed_byte_count': len(main_changed), 'changed_blocks': blocks(main_changed),
        },
        'limitations': [
            'Offline emulation models peripherals; no boot, display, radio or real flash write is executed.',
            'The BUSY line (PB3 via 0xea10) has never been observed on hardware (v10 skipped its wait, v11 never refreshed): '
            'busy_first/busy_ms10/busy_fallbacks/busy_timeouts tell which of the three cases (works / stuck low -> 3 s '
            'fallback / stuck high -> 4 s cap) the device is in.',
            'Whether the panel refresh must complete before 0xbf68 (EPD deep sleep) for the frame to hold is the physical '
            'assumption behind the wait; a fallback that is too short would show a partial frame, never brick.',
            'Runtime program/erase of slot 0 (0x2e000, past the app image) is proven on hardware by v6.',
            'Slots 1..5 (0x30000..0x3a000, flash bank 3 / SLOCK reg 5) are UNVERIFIED on hardware: the stock driver bounds '
            'them (< 0x40000) and the 184.7 KB app image already rules out a 128 KiB part, but whether bank 3 unlocks, erases '
            'and programs on this silicon -- and whether it held factory data -- only `set --slot 1` + `status` can tell.  '
            'A slot that will not program never becomes present and the picker never chooses it.',
            'The picker is a 32-bit LCG stirred with the ms tick and the ADC raw sample: good enough to shuffle six images, '
            'not a CSPRNG.',
            'v15: panel settle timing is physical.  The full-vs-partial root cause is inferred from the stock policy code (0xfa3c) and '
            'the LUT tables; rst_policy/rst_sync read back after the first wake on hardware are the proof.  The dispatch pre-wait '
            'costs one BUSY read per page change when the panel is idle and up to 4 s if BUSY is stuck high (disp_timeouts counts it).  '
            'The 20 s grace keeps the MCU awake (no WFI) for at most 20 s after a wake with no main traffic.',
        ],
    }
    (outdir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'screen_rearm': manifest['screen']['candidate_sha256'], 'screen_rearm_regions': manifest['screen']['regions'],
                      'screen_norearm': manifest['screen_norearm']['candidate_sha256'],
                      'screen_norearm_regions': manifest['screen_norearm']['regions'],
                      'screen_bytes': manifest['screen']['candidate_bytes'], 'main': manifest['main']['candidate_sha256'],
                      'out': str(outdir)}, indent=2))


if __name__ == '__main__':
    main()
