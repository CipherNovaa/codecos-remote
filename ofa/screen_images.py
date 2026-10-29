#!/usr/bin/env python3
"""Extract compiled shortcut logos or prepare PNGs in their bitmap format.

OFFLINE ONLY: this is an asset converter, not a device-logo uploader. The
stock config/screen protocol has no identified per-device bitmap upload path.
"""
import argparse
import json
import struct
from pathlib import Path
from screen_fonts import BASE, check_stock, digest

# Screen VA 0xdb54 returns these compiled image descriptors for logical key IDs.
ICONS = {0x22: ('netflix',0x2A184), 0x23: ('prime',0x2A818), 0x24: ('youtube',0x2AEAC),
         0x32: ('hbo',0x29E40), 0x34: ('rakuten',0x2AB5C), 0x36: ('disney',0x29AFC),
         0x37: ('paramount',0x2A4C8)}


def decode(raw,width,height):
    from PIL import Image
    stride=(width+7)//8
    if len(raw)!=8+stride*height:
        raise ValueError('Indexed bitmap has wrong length')
    # Stock palettes are grayscale RGBA, with opaque white then black.
    palette=[tuple(raw[i:i+4]) for i in (0,4)]
    pixels=[]
    for y in range(height):
        for x in range(width):
            bit=(raw[8+y*stride+x//8]>>(7-x%8))&1
            pixels.extend(palette[bit])
    return Image.frombytes('RGBA',(width,height),bytes(pixels))


def encode(image):
    from PIL import Image
    src=image.convert('RGBA')
    white=Image.new('RGBA',src.size,'white')
    gray=Image.alpha_composite(white,src).convert('L')
    stride=(src.width+7)//8
    raw=bytearray(b'\xff\xff\xff\xff\x00\x00\x00\xff'+bytes(stride*src.height))
    values=gray.tobytes()
    for y in range(src.height):
        for x in range(src.width):
            raw[8+y*stride+x//8]|=int(values[y*src.width+x]<128)<<(7-x%8)
    return bytes(raw)


def extract(stock,folder):
    check_stock(stock)
    folder.mkdir(parents=True,exist_ok=False)
    report=[]
    for key,(name,va) in ICONS.items():
        header,size,ptr=struct.unpack_from('<III',stock,va-BASE)
        if header&31!=7:
            raise ValueError('Unexpected LVGL image color format')
        width,height=(header>>10)&2047,header>>21
        raw=stock[ptr-BASE:ptr-BASE+size]
        decode(raw,width,height).save(folder/f'{name}.png')
        (folder/f'{name}.bin').write_bytes(raw)
        report.append(dict(key_id=hex(key),name=name,descriptor_va=hex(va),bitmap_va=hex(ptr),
                           width=width,height=height,data_size=size,sha256=digest(raw)))
    (folder/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def prepare(source,folder,width,height,resample):
    from PIL import Image,ImageOps
    if not 1<=width<=168 or not 1<=height<=384:
        raise ValueError('Requested size exceeds the 168x384 display')
    image=Image.open(source).convert('RGBA')
    image=ImageOps.contain(image,(width,height),method=getattr(Image.Resampling,resample.upper()))
    canvas=Image.new('RGBA',(width,height),'white')
    canvas.alpha_composite(image,((width-image.width)//2,(height-image.height)//2))
    raw=encode(canvas)
    preview=decode(raw,width,height)
    if encode(preview)!=raw:
        raise ValueError('Bitmap round-trip failed')
    folder.mkdir(parents=True,exist_ok=False)
    preview.save(folder/'preview.png')
    (folder/'indexed-1bpp.bin').write_bytes(raw)
    manifest=dict(status='OFFLINE ASSET ONLY; NO DEVICE LOGO UPLOAD PATH IMPLEMENTED',
        source=str(source),width=width,height=height,lvgl_color_format=7,
        lvgl_header_word=hex(7 | (width<<10) | (height<<21)),
        row_stride=(width+7)//8,palette='RGBA8888: white then black, 8 bytes total',
        bits='MSB first, row-major, each row padded to a byte; 1 = black',
        data_size=len(raw),sha256=digest(raw),resize='contain, centered, white background; threshold 128')
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def patch(base, target, source, folder, resample):
    """Replace exactly one known compiled shortcut bitmap, offline only.

    The descriptor, dimensions and allocation stay unchanged.  This works on
    the known v013f stock image and its font-only derivatives because those
    derivatives leave the shortcut bitmaps untouched.
    """
    if target not in {name for name, _ in ICONS.values()}:
        raise ValueError(f'Unknown shortcut target: {target}')
    image = bytes(base)
    match = next((key, va) for key, (name, va) in ICONS.items() if name == target)
    key, descriptor = match
    header, size, pointer = struct.unpack_from('<III', image, descriptor - BASE)
    if header & 31 != 7:
        raise ValueError('Target is not an indexed-1bpp LVGL image')
    width, height = (header >> 10) & 2047, header >> 21
    expected_size = 8 + ((width + 7) // 8) * height
    if size != expected_size:
        raise ValueError(f'Unexpected target allocation: {size} != {expected_size}')
    offset = pointer - BASE
    original = image[offset:offset + size]
    # A font-only base is permitted, but icon data must be an unchanged known
    # stock resource.  This prevents patching an arbitrary image layout.
    known_path = Path(__file__).resolve().parent / 'artifacts' / 'screen-icons' / f'{target}.bin'
    known = {digest(known_path.read_bytes())}
    if digest(original) not in known:
        raise ValueError('Target icon bytes do not match the pinned v013f resource')
    from PIL import Image, ImageOps
    src = Image.open(source).convert('RGBA')
    resized = ImageOps.contain(src, (width, height), method=getattr(Image.Resampling, resample.upper()))
    canvas = Image.new('RGBA', (width, height), 'white')
    canvas.alpha_composite(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    replacement = encode(canvas)
    if len(replacement) != size or decode(replacement, width, height).size != (width, height):
        raise ValueError('Replacement did not preserve the target allocation')
    out = bytearray(image)
    out[offset:offset + size] = replacement
    changed = [i for i, (before, after) in enumerate(zip(image, out)) if before != after]
    if any(not offset <= i < offset + size for i in changed):
        raise ValueError('Change escaped the target bitmap allocation')
    folder.mkdir(parents=True, exist_ok=False)
    candidate = folder / f'candidate-screen-icon-{target}-DO-NOT-FLASH.bin'
    candidate.write_bytes(out)
    decode(original, width, height).save(folder / 'before.png')
    decode(replacement, width, height).save(folder / 'preview.png')
    (folder / 'original-indexed-1bpp.bin').write_bytes(original)
    (folder / 'replacement-indexed-1bpp.bin').write_bytes(replacement)
    manifest = dict(status='OFFLINE CANDIDATE ONLY; REVIEW BEFORE ANY SCREEN TRANSFER',
                    base_sha256=digest(image), candidate_sha256=digest(out),
                    source=str(source), source_sha256=digest(Path(source).read_bytes()),
                    target=dict(key_id=hex(key), name=target, descriptor_va=hex(descriptor),
                                bitmap_file_offset=hex(offset), width=width, height=height,
                                allocation_bytes=size),
                    changed_byte_count=len(changed), changed_file_offsets=(None if not changed else
                        [hex(min(changed)), hex(max(changed))]),
                    invariant='Only the selected, existing LVGL bitmap allocation changes; no pointers, descriptors, code, font, or layout bytes move.',
                    limits=['This changes the selected shortcut logo globally, not per device.',
                            'No generic favourite/heart resource or runtime PNG upload protocol is established.',
                            'This does not implement a screensaver or an idle timeout.'])
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    e=sub.add_parser('extract');e.add_argument('stock',type=Path)
    c=sub.add_parser('prepare');c.add_argument('png',type=Path)
    c.add_argument('--width',type=int,required=True);c.add_argument('--height',type=int,required=True)
    c.add_argument('--resample',choices=['nearest','lanczos'],default='nearest')
    patcher=sub.add_parser('patch');patcher.add_argument('base', type=Path)
    patcher.add_argument('target', choices=[name for name, _ in ICONS.values()])
    patcher.add_argument('png', type=Path); patcher.add_argument('--resample',choices=['nearest','lanczos'],default='nearest')
    for s in (e,c,patcher):s.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args()
    if args.action == 'extract':
        result = extract(args.stock.read_bytes(), args.output_dir)
    elif args.action == 'prepare':
        result = prepare(args.png, args.output_dir, args.width, args.height, args.resample)
    else:
        result = patch(args.base.read_bytes(), args.target, args.png, args.output_dir, args.resample)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
