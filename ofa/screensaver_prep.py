#!/usr/bin/env python3
"""Preview / prepare an image for the OFA e-paper screensaver (168x384, 1-bit).

OFFLINE asset tool (no Bluetooth, nothing flashed). The e-paper strip is a tall
168x384 panel with NO grays, so every image becomes pure black+white. Photos are
dithered (Floyd-Steinberg dot patterns, like newsprint / a Kindle); logos and
bold graphics stay crisp with a hard threshold.

  compare IMG   -> one labelled sheet: contain/cover x dither/threshold, so you
                   can SEE what fits before choosing. This is the "what fits?" tool.
  prepare IMG   -> the chosen single variant: a scaled-up preview.png of exactly
                   how it renders, plus indexed-1bpp.bin (the packed LVGL bitmap,
                   same format as screen_images.py: 8-byte palette, MSB-first,
                   byte-padded rows, 1 = black ink) for the eventual over-BLE push.
"""
import argparse
from pathlib import Path

W, H = 168, 384  # e-paper panel, portrait


def fit_gray(source, mode):
    """Fit any image into a 168x384 grayscale canvas."""
    from PIL import Image, ImageOps
    src = Image.open(source).convert('RGBA')
    flat = Image.alpha_composite(Image.new('RGBA', src.size, 'white'), src).convert('L')
    if mode == 'cover':                       # fill the strip, crop the overflow
        return ImageOps.fit(flat, (W, H), method=Image.Resampling.LANCZOS)
    contained = ImageOps.contain(flat, (W, H), method=Image.Resampling.LANCZOS)  # whole image, white bars
    canvas = Image.new('L', (W, H), 255)
    canvas.paste(contained, ((W - contained.width) // 2, (H - contained.height) // 2))
    return canvas


def one_bit(gray, method, invert=False):
    """Grayscale -> 1-bit ('1' mode: 0=black, 255=white)."""
    if invert:
        from PIL import ImageOps
        gray = ImageOps.invert(gray)
    if method == 'dither':
        return gray.convert('1')              # PIL default = Floyd-Steinberg
    return gray.point(lambda p: 255 if p >= 128 else 0).convert('1')


def pack_1bpp(onebit):
    """Pack a '1'-mode image into the LVGL indexed-1bpp bitmap the screen uses.

    8-byte palette (opaque white then black), then MSB-first, byte-padded rows;
    bit = 1 means palette[1] = black ink. Matches screen_images.py exactly.
    """
    stride = (W + 7) // 8
    raw = bytearray(b'\xff\xff\xff\xff\x00\x00\x00\xff' + bytes(stride * H))
    px = onebit.load()
    for y in range(H):
        for x in range(W):
            if px[x, y] == 0:                 # black pixel -> set the bit
                raw[8 + y * stride + x // 8] |= 1 << (7 - x % 8)
    return bytes(raw)


def preview(onebit, scale, label):
    """Upscale (nearest) with a caption bar so a phone screen can judge it."""
    from PIL import Image, ImageDraw
    big = onebit.convert('L').resize((W * scale, H * scale), Image.Resampling.NEAREST)
    pad, bar = 6, 22
    canvas = Image.new('RGB', (big.width + pad * 2, big.height + pad * 2 + bar), (32, 32, 32))
    canvas.paste(big.convert('RGB'), (pad, pad + bar))
    d = ImageDraw.Draw(canvas)
    d.rectangle([pad, pad + bar, pad + big.width - 1, pad + bar + big.height - 1], outline=(90, 90, 90))
    d.text((pad + 2, 4), label, fill=(230, 230, 230))
    return canvas


def compare(source, out, scale):
    from PIL import Image, ImageDraw
    variants = [('contain', 'dither'), ('contain', 'threshold'),
                ('cover', 'dither'), ('cover', 'threshold')]
    tiles = [preview(one_bit(fit_gray(source, m), meth), scale, f'{m} / {meth}')
             for m, meth in variants]
    gap, top = 14, 30
    tw, th = tiles[0].width, tiles[0].height
    sheet = Image.new('RGB', (tw * len(tiles) + gap * (len(tiles) + 1),
                              th + top + gap), (20, 20, 20))
    ImageDraw.Draw(sheet).text((gap, 9), f'{Path(source).name}  ->  168x384 e-paper, 1-bit', fill=(255, 255, 255))
    for i, t in enumerate(tiles):
        sheet.paste(t, (gap + i * (tw + gap), top))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def prepare(source, folder, mode, method, invert, scale):
    from PIL import Image
    ob = one_bit(fit_gray(source, mode), method, invert)
    raw = pack_1bpp(ob)
    folder.mkdir(parents=True, exist_ok=True)
    preview(ob, scale, f'{mode} / {method}{" / invert" if invert else ""}').save(folder / 'preview.png')
    ob.save(folder / 'actual-size.png')
    (folder / 'indexed-1bpp.bin').write_bytes(raw)
    print(f'wrote {folder}/preview.png, actual-size.png, indexed-1bpp.bin ({len(raw)} bytes)')
    return folder


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('compare'); c.add_argument('image', type=Path)
    c.add_argument('--out', type=Path, required=True); c.add_argument('--scale', type=int, default=2)
    pr = sub.add_parser('prepare'); pr.add_argument('image', type=Path)
    pr.add_argument('--output-dir', type=Path, required=True)
    pr.add_argument('--mode', choices=['contain', 'cover'], default='cover')
    pr.add_argument('--method', choices=['dither', 'threshold'], default='dither')
    pr.add_argument('--invert', action='store_true'); pr.add_argument('--scale', type=int, default=3)
    a = p.parse_args()
    if a.action == 'compare':
        print(compare(a.image, a.out, a.scale))
    else:
        prepare(a.image, a.output_dir, a.mode, a.method, a.invert, a.scale)


if __name__ == '__main__':
    main()
