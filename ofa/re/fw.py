"""Shared helpers for the favorites RE: load the three screen images, diff, disasm, find BL callers."""
import struct, sys
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS

ROOT = Path(__file__).resolve().parents[1]   # the ofa/ toolkit dir
STOCK = (ROOT / 'artifacts/apk/x/assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin').read_bytes()
V15 = (ROOT / 'artifacts/screensaver-v15/screen-screensaver-v15-NOREARM-DO-NOT-FLASH.bin').read_bytes()
HOLD = (ROOT / 'artifacts/touchscreen-hold-screen/screen-touchscreen-hold-all-keys-DO-NOT-FLASH.bin').read_bytes()
VA0 = 0x1000

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
md.detail = False


def off(va):
    return va - VA0


def u32(img, va):
    return struct.unpack_from('<I', img, off(va))[0]


def u16(img, va):
    return struct.unpack_from('<H', img, off(va))[0]


def disasm(img, va, n_bytes=None, count=None, stop_at=None):
    data = img[off(va): off(va) + (n_bytes or 0x400)]
    out = []
    for i, ins in enumerate(md.disasm(data, va)):
        out.append(f'{ins.address:#07x}  {ins.bytes.hex():<10} {ins.mnemonic:<8} {ins.op_str}')
        if count and i + 1 >= count:
            break
        if stop_at and ins.address >= stop_at:
            break
    return '\n'.join(out)


def diff_ranges(a, b, gap=8):
    """Return [(start_va, end_va_exclusive)] of differing bytes, merging gaps < gap."""
    ranges = []
    i = 0
    n = min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            s = i
            last = i
            while i < n and (a[i] != b[i] or i - last < gap):
                if a[i] != b[i]:
                    last = i
                i += 1
            ranges.append((s + VA0, last + 1 + VA0))
        else:
            i += 1
    return ranges


def bl_targets(img, lo=VA0, hi=None):
    """Manual decode of T1 BL at every halfword alignment. Returns list of (site_va, target_va)."""
    hi = hi or (VA0 + len(img))
    res = []
    for va in range(lo, hi - 2, 2):
        h1 = u16(img, va)
        if (h1 & 0xF800) != 0xF000:
            continue
        h2 = u16(img, va + 2)
        if (h2 & 0xD000) != 0xD000:
            continue
        S = (h1 >> 10) & 1
        imm10 = h1 & 0x3FF
        J1 = (h2 >> 13) & 1
        J2 = (h2 >> 11) & 1
        imm11 = h2 & 0x7FF
        I1 = 1 - (J1 ^ S)
        I2 = 1 - (J2 ^ S)
        imm = (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1)
        if S:
            imm -= 1 << 25
        tgt = va + 4 + imm
        res.append((va, tgt))
    return res


def callers(img, target, lo=VA0, hi=None):
    return [s for s, t in bl_targets(img, lo, hi) if t == target]


def pool_refs(img, value, lo=VA0, hi=None):
    """Find 32-bit aligned words equal to value (pool words)."""
    hi = hi or (VA0 + len(img))
    res = []
    for va in range(lo & ~3, hi - 3, 4):
        if u32(img, va) == value:
            res.append(va)
    return res


def strings(img, minlen=4):
    res = []
    cur = bytearray()
    start = 0
    for i, b in enumerate(img):
        if 32 <= b < 127:
            if not cur:
                start = i
            cur.append(b)
        else:
            if len(cur) >= minlen:
                res.append((start + VA0, cur.decode()))
            cur = bytearray()
    return res
