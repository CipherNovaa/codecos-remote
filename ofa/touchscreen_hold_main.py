"""Build the fixed-size touchscreen edge/IR candidate, offline only."""
from screensaver_firmware import (ROOT, assemble_stable, capstone_ok, realtek_digest,
                                  sha256, SHA_OFFSET, thumb_bl)

B = ROOT / 'artifacts/codecos-v16/main-codecos-v16-DO-NOT-FLASH.bin'
SHA = '957c9549df23f1a2e8952f328a1eac7bf9e242b2aabca88dc4cf66f584b70300'
VA = 0x80de00
HID_SITE = 0x81d17c
IR_SITE, IR_DISPATCH, IR_CONTINUE = 0x81b002, 0x81d082, 0x81b006
STUB, STUB_LIMIT = 0x827416, 0x827470


def va_off(va):
    return va - VA


def _no_inbound_branch(base):
    """The selected padding must not be a stock code target."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    for ins in md.disasm(base, VA):
        if ins.mnemonic not in ('b', 'bl', 'blx', 'cbz', 'cbnz'):
            continue
        try:
            destination = int(ins.op_str.rsplit('#', 1)[1], 0)
        except (IndexError, ValueError):
            continue
        if STUB <= destination < STUB_LIMIT:
            raise ValueError(f'stock branch at {ins.address:#x} enters trampoline padding')


def build_inplace(base):
    if sha256(base) != SHA:
        raise ValueError('main base SHA is not codecOS-v16')
    hid_off, ir_off, stub_off = map(va_off, (HID_SITE, IR_SITE, STUB))
    if base[hid_off:hid_off + 12] != bytes.fromhex('2846fff7c5fc0020fff7c2fc'):
        raise ValueError('variant-4 HID tail no longer has its audited stock bytes')
    if base[ir_off:ir_off + 4] != thumb_bl(IR_SITE, IR_DISPATCH):
        raise ValueError('0x43-frame IR branch is not the audited call')
    if any(base[stub_off:va_off(STUB_LIMIT)]):
        raise ValueError('IR trampoline region is not all-zero padding')
    _no_inbound_branch(base)

    code, syms = assemble_stable('cortex-m4', 'touchscreen_hold_main.ld', 'hold_ir_gate',
                                 ['touchscreen_hold_main.S'])
    if syms.get('hold_ir_gate') != STUB or len(code) > STUB_LIMIT - STUB:
        raise ValueError('IR trampoline moved or exceeds its verified padding run')
    capstone_ok(code, STUB, {'hold_ir_gate': STUB})

    out = bytearray(base)
    # Existing variant-4 HID gate: byte8=1 sends keycode; byte8=0 sends zero.
    out[hid_off:hid_off + 12] = bytes.fromhex('002f14bf28460020fff7c2fc')
    # Only the touchscreen 0x43 IR call is redirected through the byte8 gate.
    out[ir_off:ir_off + 4] = thumb_bl(IR_SITE, STUB)
    out[stub_off:stub_off + len(code)] = code
    out[SHA_OFFSET:SHA_OFFSET + 32] = realtek_digest(out)
    changed = {i for i, (x, y) in enumerate(zip(base, out)) if x != y}
    allowed = (set(range(hid_off, hid_off + 12)) | set(range(ir_off, ir_off + 4)) |
               set(range(stub_off, stub_off + len(code))) |
               set(range(SHA_OFFSET, SHA_OFFSET + 32)))
    if changed - allowed or len(out) != len(base):
        raise ValueError('main candidate changed outside the in-place allowlist')
    return bytes(out)


def main():
    out = build_inplace(B.read_bytes())
    directory = ROOT / 'artifacts/touchscreen-hold-main'
    directory.mkdir(exist_ok=True)
    path = directory / 'main-touchscreen-hold-inplace-DO-NOT-FLASH.bin'
    path.write_bytes(out)
    print(sha256(out), len(out), 'in-place')


if __name__ == '__main__':
    main()
