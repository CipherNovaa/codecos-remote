#!/usr/bin/env python3
"""Execute original RT150T ARM font callbacks against the offline font candidate."""
import argparse
import struct
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR
from screen_fonts import BASE, FACES, BUTTON_LABELS, build_candidate, font_info, glyph, formatted_labels


def call(cpu, address, *args):
    for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
        cpu.reg_write(reg, value)
    cpu.reg_write(UC_ARM_REG_SP, 0x2000E000)
    cpu.reg_write(UC_ARM_REG_LR, 0x3F001)
    cpu.emu_start(address | 1, 0x3F000, count=50000)
    return cpu.reg_read(UC_ARM_REG_R0)


def verify(data):
    cpu=Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(0,0x40000)
    cpu.mem_write(BASE,data)
    cpu.mem_map(0x20000000,0x10000)
    count=0
    for offset,end in FACES:
        info=font_info(data,offset)
        assert info['compressed']==0 and info['kern']==0
        descriptor,bitmap=struct.unpack_from('<II',data,offset)
        for cp,gid in info['mapping'].items():
            g=glyph(data,info,gid)
            assert info['bitmap']+g['index']+(g['width']*g['height']*info['bpp']+7)//8<=end
            ptr=call(cpu,bitmap,BASE+offset,cp)
            assert ptr == BASE+info['bitmap']+g['index'], (cp,hex(ptr),g)
            cpu.mem_write(0x2000C000,bytes(16))
            assert call(cpu,descriptor,BASE+offset,0x2000C000,cp,0)==1
            raw=bytes(cpu.mem_read(0x2000C000,16))
            adv,w,h,x,y=struct.unpack_from('<HHHhh',raw,4)
            assert (adv,w,h,x,y)==((g['advance']+8)//16,g['width'],g['height'],g['x'],g['y'])
            assert raw[14]&15==info['bpp']
            count+=1
        assert call(cpu,bitmap,BASE+offset,0x1234)==0
    # The screen's real getter selects custom text by page-position, independent
    # of the key ID. NUL-terminated adjacent records stay independent.
    cpu.mem_write(0x20000259,b'\x02\xfa\xfb')
    cpu.mem_write(0x2000026E,b'ABCDEFGHIJKLMNO\x00'+b'SUBS'.ljust(16,b'\x00'))
    assert call(cpu,0xD9A4,0)==0x2000026E
    assert call(cpu,0xD9A4,1)==0x2000027E
    assert call(cpu,0x123E,0x2000026E)==15  # strlen used by the screen formatter
    from screen_images import ICONS
    cpu.mem_write(0x20000253,b'\x40')  # enable built-in app logos
    for key in range(256):
        ptr=call(cpu,0xDB54,key)
        assert ptr==(ICONS[key][1] if key in ICONS else 0)
    cpu.mem_write(0x20000253,b'\x00')
    for key in ICONS:
        assert call(cpu,0xDB54,key)==0
    return count


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stock',type=Path)
    p.add_argument('--font',type=Path,required=True)
    p.add_argument('--font-size',type=int,default=22)
    p.add_argument('--width-scale',type=float,default=1.0)
    args=p.parse_args()
    stock=args.stock.read_bytes()
    candidate,manifest=build_candidate(stock,args.font,args.font_size,args.width_scale)
    n=verify(stock)+verify(candidate)
    print(f'PASS: {n} actual ARM font lookup/metrics/bitmap callbacks across stock and patched faces')
    print('PASS: font storage bounds and independent NUL-terminated custom-label lookup')
    print('PASS: all 256 icon IDs use only the seven compiled images; logo-disable flag respected')
    # Regression for the separate width table: valid LVGL font callbacks alone
    # did not catch v1's overflowing labels. Exercise actual formatter code.
    info = font_info(candidate, 0x2BD7C)
    labels = list(BUTTON_LABELS) + [chr(cp)*15 for cp in range(32, 127)]
    labels += ['A LONG LABEL', 'ABCDEFGHIJKLMNO', 'MODE / SUBS', '  AUDIO  INFO ']
    for original, wrapped in zip(labels, formatted_labels(candidate, labels)):
        assert wrapped.replace('\n', '').replace(' ', '') == original.replace(' ', '')
        for line in wrapped.split('\n'):
            width = sum((glyph(candidate, info, info['mapping'].get(ord(c),1))['advance']+8)//16 for c in line)
            assert width < 77, (original, wrapped, width)
    print(f'PASS: {len(labels)} actual ARM label-wrap cases; current labels and printable ASCII fit the width')
    print(manifest['candidate_sha256'])
    print('Offline only. Screen recovery and hardware display verification still required; do not flash.')


if __name__=='__main__':
    main()
