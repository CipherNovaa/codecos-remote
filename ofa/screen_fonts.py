#!/usr/bin/env python3
"""Offline RT150T font inspection/patching. NEVER uploads or flashes.

The three OFA text faces use LVGL fmt_txt. Replace their supported ASCII
characters with MGS1 Codec, preserving cmaps, glyph IDs and callbacks.
Update the separate label-wrapping widths to match the new glyph advances.
The candidate is withheld from flashing until screen recovery is established.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

SCREEN_SHA256 = '64670a392082daa0d708eb1f5f5ff58227e7d01d60da480ed56b157f5a60980d'
BASE = 0x1000
# (font object, end of bitmap allocation). File offsets, not MCU addresses.
FACES = ((0x2BD98, 0x246C1), (0x2BD7C, 0x24C2B), (0x2BD60, 0x253AC))
WRAP_WIDTHS = 0x1E9F4  # 91 u32 widths for ASCII 32..122, read by VA d844.
WRAP_FALLBACK = 0xC856  # movs r0, #6: width for all other input bytes.
BUTTON_LABELS = ('MODE', 'SUBS', 'AUDIO', 'SPARE', 'RETURN', 'INFO')
# The stock punctuation cmap maps '%' to the blank glyph.  Reuse one
# non-ASCII glyph slot (U+00A0) for a rendered percent sign, preserving every
# printable-ASCII glyph already present in the stock cmap.
PERCENT_GID = 79


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_stock(data):
    if digest(data) != SCREEN_SHA256:
        raise ValueError('Not the pinned RT150T v013f screen asset')


def font_info(data, offset):
    desc = struct.unpack_from('<I', data, offset + 16)[0] - BASE
    bitmap, glyphs, cmaps, kern = struct.unpack_from('<4I', data, desc)
    flags = struct.unpack_from('<H', data, desc + 18)[0]
    mapping = {}
    for i in range(flags & 511):
        start, length, first, uni, offsets, count, kind = struct.unpack_from(
            '<IHHIIHB', data, cmaps - BASE + i * 20)
        if kind == 0:
            mapping.update((start + j, first + data[offsets - BASE + j]) for j in range(length))
        elif kind == 2:
            mapping.update((start + j, first + j) for j in range(length))
        elif kind == 3:
            mapping.update((start + struct.unpack_from('<H', data, uni - BASE + 2*j)[0], first + j)
                           for j in range(count))
        else:
            raise ValueError(f'Unsupported cmap format {kind}')
    return dict(offset=offset, desc=desc, bitmap=bitmap-BASE, glyphs=glyphs-BASE,
                bpp=(flags >> 9) & 15, compressed=flags >> 14,
                kern=kern, mapping=mapping,
                line_height=struct.unpack_from('<H', data, offset+8)[0],
                baseline=struct.unpack_from('<H', data, offset+10)[0])


def glyph(data, info, gid):
    value, width, height, x, y = struct.unpack_from('<IBBbb', data, info['glyphs'] + gid*8)
    index, advance = value & 0xFFFFF, value >> 20
    count, bpp = width*height, info['bpp']
    if info['compressed'] or bpp not in (1, 2, 4, 8):
        raise ValueError('Unsupported glyph encoding')
    raw = data[info['bitmap']+index:info['bitmap']+index+(count*bpp+7)//8]
    pixels = [((raw[i*bpp//8] >> (8-bpp-(i*bpp%8))) & ((1<<bpp)-1))*255//((1<<bpp)-1)
              for i in range(count)]
    return dict(width=width, height=height, x=x, y=y, advance=advance,
                index=index, pixels=pixels)


def render(ttf, codepoint, width_scale=1.0):
    from PIL import Image, ImageDraw
    char = chr(codepoint)
    x0, y0, x1, y1 = ttf.getbbox(char, anchor='ls')
    canvas = Image.new('L', (max(1,x1-x0), max(1,y1-y0)))
    ImageDraw.Draw(canvas).text((-x0,-y0), char, font=ttf, fill=255, anchor='ls')
    bounds = canvas.getbbox()
    result = dict(advance=round(ttf.getlength(char)*width_scale*16), index=0)
    if bounds is None:
        result.update(width=0, height=0, x=0, y=0, pixels=[])
    else:
        canvas = canvas.crop(bounds)
        scaled_width = max(1, round(canvas.width * width_scale))
        if scaled_width != canvas.width:
            canvas = canvas.resize((scaled_width, canvas.height), Image.Resampling.NEAREST)
        result.update(width=canvas.width, height=canvas.height,
                      x=round((x0+bounds[0]) * width_scale), y=-(y0+bounds[3]),
                      pixels=[255 if v >=128 else 0 for v in canvas.tobytes()])
    return result


def packed(pixels):
    result = bytearray((len(pixels)+7)//8)
    for i, value in enumerate(pixels):
        result[i//8] |= int(value >=128) << (7-i%8)
    return bytes(result)


def build_candidate(stock, ttf_path, source_size=22, width_scale=1.0):
    from PIL import ImageFont
    check_stock(stock)
    if type(source_size) is not int or not 16 <= source_size <= 32:
        raise ValueError('Font source size must be an integer in 16..32')
    if not 0.7 <= width_scale <= 1.0:
        raise ValueError('Width scale must be in 0.7..1.0')
    font = ImageFont.truetype(str(ttf_path), source_size)
    if font.getname()[0] != 'MGS1 Codec':
        raise ValueError('Expected the MGS1 Codec font')
    out = bytearray(stock)
    allow, report = set(), []
    for offset, end in FACES:
        info = font_info(stock, offset)
        if info['kern'] or info['bpp'] != 2 or info['compressed']:
            raise ValueError('Unexpected OFA face layout')
        canonical = {}
        for cp, gid in info['mapping'].items():
            canonical.setdefault(gid, cp)
        blob, seen = bytearray(), {}
        changed = []
        for gid in range(1, max(canonical)+1):
            cp = canonical[gid]
            original = glyph(stock, info, gid)
            # U+00A0's glyph slot is not used by printable ASCII.  It is
            # repurposed for '%' because stock maps '%' to blank glyph 1.
            render_cp = 37 if gid == PERCENT_GID else cp
            g = render(font, render_cp, width_scale) if 32 <= render_cp < 127 else original
            if 32 <= render_cp < 127:
                changed.append(cp)
                if g['height']+g['y'] > info['line_height']-info['baseline'] or -g['y'] > info['baseline']:
                    raise ValueError(f'U+{cp:04X} does not fit original font line metrics')
            raw = packed(g['pixels'])
            if raw not in seen:
                seen[raw] = len(blob)
                blob.extend(raw)
            value = seen[raw] | (g['advance'] << 20)
            at = info['glyphs']+8*gid
            struct.pack_into('<IBBbb', out, at, value, g['width'],g['height'],g['x'],g['y'])
            allow.update(range(at, at+8))
        capacity = end-info['bitmap']
        if len(blob)>capacity:
            raise ValueError(f'Font allocation exceeded: {len(blob)} > {capacity}')
        out[info['bitmap']:end] = blob + bytes(capacity-len(blob))
        allow.update(range(info['bitmap'],end))
        # Same four cmaps; bpp becomes 1. All remaining flags are unchanged.
        at = info['desc']+18
        flags = struct.unpack_from('<H',out,at)[0]
        struct.pack_into('<H',out,at,(flags & ~(15<<9)) | (1<<9))
        allow.update(range(at,at+2))
        report.append(dict(font_file_offset=hex(offset),bitmap_bytes=len(blob),capacity=capacity,
                           ascii_codepoints=changed,line_height=info['line_height'],baseline=info['baseline']))
        # ASCII 32..63 cmap: make '%' (offset 5) select the repurposed glyph.
        cmap = struct.unpack_from('<I', out, info['desc'] + 8)[0] - BASE
        start, length, first, uni, offsets, count, kind = struct.unpack_from('<IHHIIHB', out, cmap)
        if (start, length, first, kind) != (32, 32, 1, 0):
            raise ValueError('Unexpected punctuation cmap')
        at = offsets - BASE + (37 - start)
        out[at] = PERCENT_GID - first
        allow.add(at)
    # The screen's formatter does NOT ask LVGL for character advances. Its
    # independent table must agree with the custom-button face, or labels can
    # overflow despite valid font callbacks. VA d844 has no other call sites.
    old_info = font_info(stock, 0x2BD7C)
    new_info = font_info(out, 0x2BD7C)
    for cp in range(32, 123):
        at = WRAP_WIDTHS + 4 * (cp - 32)
        old_glyph = glyph(stock, old_info, old_info['mapping'].get(cp, 1))
        # Stock estimates every punctuation/space byte as six pixels; only
        # letters and digits track the original glyph advances exactly.
        old_width = (old_glyph['advance'] + 8) // 16 if chr(cp).isalnum() else 6
        if struct.unpack_from('<I', stock, at)[0] != old_width:
            raise ValueError(f'Unexpected original wrapping width for U+{cp:04X}')
        new_glyph = glyph(out, new_info, new_info['mapping'].get(cp, 1))
        struct.pack_into('<I', out, at, (new_glyph['advance'] + 8) // 16)
        allow.update(range(at, at + 4))
    if stock[WRAP_FALLBACK:WRAP_FALLBACK + 2] != b'\x06\x20':
        raise ValueError('Unexpected wrapping fallback instruction')
    space_advance = (glyph(out, new_info, 1)['advance'] + 8) // 16
    out[WRAP_FALLBACK] = space_advance
    allow.add(WRAP_FALLBACK)
    if len(out)!=len(stock) or any(a!=b and i not in allow for i,(a,b) in enumerate(zip(stock,out))):
        raise ValueError('Change outside font allocation allowlist')
    return bytes(out), dict(status='OFFLINE ONLY; SCREEN RECOVERY NOT ESTABLISHED; DO NOT FLASH',
        stock_sha256=digest(stock),candidate_sha256=digest(out),ttf_sha256=digest(ttf_path.read_bytes()),
        size=len(out),faces=report,
        glyph_format='1 bpp alpha, MSB first, continuous row-major bits; each glyph starts at a byte',
        source_size=source_size, cap_height=render(font, ord('M'))['height'],
        width_scale=width_scale,
        typical_advance=render(font, ord('M'), width_scale)['advance']/16,
        wrapping=dict(table_file_offset=hex(WRAP_WIDTHS),count=91,
                      fallback_instruction_file_offset=hex(WRAP_FALLBACK),
                      fallback_width=space_advance),
        limitations=['Only three OFA text faces changed; two general LVGL faces retained',
                     'Non-ASCII glyph shapes retained but quantized from 2 bpp to 1 bpp',
                     'Unsupported stock punctuation still maps to the space glyph',
                     'No live boot, display or recovery verification; no upload command provided'])


def preview(data, output):
    from PIL import Image, ImageDraw
    # Render using decoded firmware metrics, not the source TTF.
    samples = ['DESKTOP', 'MODE SUBS AUDIO', '0123456789', 'RED GREEN BLUE']
    image = Image.new('L',(336, len(FACES)*110),255)
    draw = ImageDraw.Draw(image)
    for row,(offset,_) in enumerate(FACES):
        info = font_info(data,offset)
        draw.text((4,row*110),f'font {offset:05x} / {info["bpp"]} bpp',fill=0)
        for line,text in enumerate(samples):
            x,base=4,row*110+32+line*22
            for char in text:
                g=glyph(data,info,info['mapping'].get(ord(char),1))
                if g['width'] and g['height']:
                    tile=Image.frombytes('L',(g['width'],g['height']),bytes(255-v for v in g['pixels']))
                    image.paste(tile,(round(x)+g['x'],base-g['height']-g['y']))
                x+=g['advance']/16
    image.save(output)


def formatted_labels(data, labels=BUTTON_LABELS):
    """Run the screen's actual wrapping routine, including its width lookup."""
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(0, 0x40000)
    cpu.mem_write(BASE, data)
    cpu.mem_map(0x20000000, 0x10000)
    result = []
    for label in labels:
        encoded = label.encode('ascii')
        if not 1 <= len(encoded) <= 15 or any(c < 32 or c > 126 for c in encoded):
            raise ValueError('Labels must contain 1..15 printable ASCII characters')
        cpu.mem_write(0x2000C000, encoded + b'\0')
        cpu.mem_write(0x2000C100, b'\xa5' * 64)
        for reg, value in ((UC_ARM_REG_R0, 0x2000C100), (UC_ARM_REG_R1, 0x2000C000),
                           (UC_ARM_REG_SP, 0x2000E000), (UC_ARM_REG_LR, 0x3F001)):
            cpu.reg_write(reg, value)
        cpu.emu_start(0xD26B, 0x3F000, count=50000)
        if cpu.reg_read(UC_ARM_REG_PC) != 0x3F000:
            raise ValueError('Screen formatter did not return')
        raw = bytes(cpu.mem_read(0x2000C100, 64))
        if b'\0' not in raw[:31] or raw[31:] != b'\xa5' * 33:
            raise ValueError('Screen formatter exceeded its output buffer')
        result.append(raw.split(b'\0', 1)[0].decode('ascii'))
    return result


def page_preview(data, output):
    """Layout illustration at native size; font and wrapping are firmware-derived.

    Borders/centering are illustrative, not an emulation of the LVGL renderer.
    """
    from PIL import Image, ImageDraw
    info = font_info(data, 0x2BD7C)
    labels = formatted_labels(data)
    image = Image.new('L', (168, 280), 255)
    draw = ImageDraw.Draw(image)
    for i, label in enumerate(labels):
        left, top = (i % 2) * 85, (i // 2) * 71
        draw.rectangle((left, top, left + 81, top + 67), outline=0)
        lines = label.split('\n')
        baseline = top + (68 - len(lines)*info['line_height'])//2 + info['line_height'] - info['baseline']
        for line in lines:
            glyphs = [glyph(data, info, info['mapping'].get(ord(c), 1)) for c in line]
            width = sum((g['advance'] + 8)//16 for g in glyphs)
            if width >= 77:
                raise ValueError(f'Wrapped line still exceeds the label width: {line!r}')
            x = left + (82 - width)//2
            for g in glyphs:
                if g['width'] and g['height']:
                    tile = Image.frombytes('L', (g['width'], g['height']), bytes(255-v for v in g['pixels']))
                    image.paste(tile, (x+g['x'], baseline-g['height']-g['y']))
                x += (g['advance'] + 8)//16
            baseline += info['line_height']
    image.save(output)
    return dict(zip(BUTTON_LABELS, labels))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stock',type=Path)
    p.add_argument('--font',type=Path,required=True)
    p.add_argument('--font-size',type=int,default=22,
                   help='TTF source size (16..32); 22 fits all six current labels on one line')
    p.add_argument('--width-scale',type=float,default=1.0,
                   help='Horizontal glyph and advance scale (0.7..1.0)')
    p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args()
    stock=args.stock.read_bytes()
    candidate,manifest=build_candidate(stock,args.font,args.font_size,args.width_scale)
    args.output_dir.mkdir(parents=True,exist_ok=False)
    (args.output_dir/'candidate-screen-font-DO-NOT-FLASH.bin').write_bytes(candidate)
    preview(stock,args.output_dir/'stock-fonts.png')
    preview(candidate,args.output_dir/'mgs-fonts.png')
    page_preview(stock,args.output_dir/'stock-page2.png')
    manifest['page2_wrapping'] = page_preview(candidate,args.output_dir/'mgs-page2.png')
    (args.output_dir/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
