"""Image rendering — screensaver_prep, run off the paint thread via the executor.

Everything here is pure CPU (PIL): fit → 1-bit → pack. We await it on the qasync
loop's default ThreadPoolExecutor so a big pack (64512 px) never freezes the UI.
Rendering returns PNG *bytes* (built on the worker thread) and the panel turns them
into a QPixmap on the main thread — QImage/QPixmap construction stays UI-side.
"""
import asyncio
import io

import screensaver_prep as sp

W, H = sp.W, sp.H          # 168 x 384


async def _in_executor(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn, *args)


def _render_png_bytes(source, mode, method, invert, upscale):
    from PIL import Image
    ob = sp.one_bit(sp.fit_gray(source, mode), method, invert)   # PIL mode '1'
    img = ob.convert("L")
    if upscale > 1:
        img = img.resize((W * upscale, H * upscale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def render_png(source, mode, method, invert=False, upscale=1):
    """1-bit render of `source` as PNG bytes (native 168xH, optionally nearest-upscaled)."""
    return await _in_executor(_render_png_bytes, source, mode, method, invert, upscale)


async def compare_sheet(source, out_path, scale=2):
    """Write the 4-quadrant contain/cover × dither/threshold sheet. Returns the Path."""
    from pathlib import Path
    return await _in_executor(sp.compare, source, Path(out_path), scale)


async def prepare_variant(source, folder, mode, method, invert=False, scale=3):
    """Write preview.png + actual-size.png + indexed-1bpp.bin. Returns the folder Path."""
    from pathlib import Path
    return await _in_executor(sp.prepare, source, Path(folder), mode, method, invert, scale)


def _blob_size(source, mode, method, invert):
    return len(sp.pack_1bpp(sp.one_bit(sp.fit_gray(source, mode), method, invert)))


async def packed_size(source, mode, method, invert=False):
    """Bytes the packed slot blob would be (should equal constants.EXPECT_1BPP)."""
    return await _in_executor(_blob_size, source, mode, method, invert)
