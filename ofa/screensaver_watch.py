#!/usr/bin/env python3
"""CODEC e-paper screensaver — image previewer (laptop, offline).

Shows how images look on the remote's tall 168x384 black+white e-paper strip, four
ways each (contain/cover x dither/threshold). No Bluetooth, nothing flashed.

  codec-screensaver-preview <folder>       BATCH: preview every image in a folder,
                                           then open ONE window to flip with the arrow keys.
  codec-screensaver-preview <folder> -w    same, but also keep watching it for new drops.
  codec-screensaver-preview                WATCH the default drop folder (~/Screensaver-Drop):
                                           drop images in, they appear in the one open window.
  codec-screensaver-preview a.jpg b.png    preview specific files.

Previews go in a "_previews/" subfolder and open in ONE viewer (Gwenview if installed
— it auto-refreshes as new ones render — otherwise feh; both flip with Left/Right).
"""
import atexit
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

W, H = 168, 384
DROP = Path.home() / "Screensaver-Drop"
PREVIEW_DIR = "_previews"
SUFFIX = ".SAVER.png"
EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif"}


# ---------- rendering ----------
def fit_gray(source, mode):
    from PIL import Image, ImageOps
    src = Image.open(source).convert("RGBA")
    flat = Image.alpha_composite(Image.new("RGBA", src.size, "white"), src).convert("L")
    if mode == "cover":
        return ImageOps.fit(flat, (W, H), method=Image.Resampling.LANCZOS)
    contained = ImageOps.contain(flat, (W, H), method=Image.Resampling.LANCZOS)
    canvas = Image.new("L", (W, H), 255)
    canvas.paste(contained, ((W - contained.width) // 2, (H - contained.height) // 2))
    return canvas


def one_bit(gray, method):
    if method == "dither":
        return gray.convert("1")
    return gray.point(lambda p: 255 if p >= 128 else 0).convert("1")


def tile(onebit, scale, label):
    from PIL import Image, ImageDraw
    big = onebit.convert("L").resize((W * scale, H * scale), Image.Resampling.NEAREST)
    pad, bar = 6, 22
    canvas = Image.new("RGB", (big.width + pad * 2, big.height + pad * 2 + bar), (32, 32, 32))
    canvas.paste(big.convert("RGB"), (pad, pad + bar))
    d = ImageDraw.Draw(canvas)
    d.rectangle([pad, pad + bar, pad + big.width - 1, pad + bar + big.height - 1], outline=(90, 90, 90))
    d.text((pad + 2, 4), label, fill=(230, 230, 230))
    return canvas


def render(source, outdir, scale=2):
    from PIL import Image, ImageDraw
    out = outdir / (Path(source).stem + SUFFIX)
    if out.exists() and out.stat().st_mtime >= Path(source).stat().st_mtime:
        return out                                   # cached; source unchanged
    variants = [("contain", "dither"), ("contain", "threshold"),
                ("cover", "dither"), ("cover", "threshold")]
    tiles = [tile(one_bit(fit_gray(source, m), meth), scale, f"{m} / {meth}") for m, meth in variants]
    gap, top = 14, 30
    tw, th = tiles[0].width, tiles[0].height
    sheet = Image.new("RGB", (tw * len(tiles) + gap * (len(tiles) + 1), th + top + gap), (20, 20, 20))
    ImageDraw.Draw(sheet).text((gap, 9), f"{Path(source).name}   ->   168x384 e-paper, 1-bit", fill=(255, 255, 255))
    for i, t in enumerate(tiles):
        sheet.paste(t, (gap + i * (tw + gap), top))
    outdir.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def is_input(p):
    return (p.is_file() and p.suffix.lower() in EXTS
            and not p.name.endswith(SUFFIX) and not p.name.startswith("."))


def render_folder(folder):
    outdir = folder / PREVIEW_DIR
    inputs = sorted(p for p in folder.iterdir() if is_input(p))
    if not inputs:
        print(f"  no images found in {folder}")
        return outdir, []
    made = []
    for i, p in enumerate(inputs, 1):
        try:
            made.append(render(p, outdir))
            print(f"  [{i}/{len(inputs)}] {p.name}", flush=True)
        except Exception as e:
            print(f"  [{i}/{len(inputs)}] SKIP {p.name}: {e}", flush=True)
    return outdir, made


# ---------- single viewer window ----------
def _viewer():
    if shutil.which("gwenview"):
        return "gwenview"
    if shutil.which("feh"):
        return "feh"
    return "xdg-open"


_view_proc = None


def open_viewer(target, force=False):
    """Open ONE viewer window on a folder (or list of files). Never stacks."""
    global _view_proc
    if _view_proc is not None and _view_proc.poll() is None and not force:
        return                                       # already open
    kind = _viewer()
    if kind == "gwenview":
        args = ["gwenview", str(target)] if isinstance(target, Path) else ["gwenview", *map(str, target)]
    elif kind == "feh":
        base = ["feh", "--scale-down", "--auto-zoom", "--sort", "filename"]
        args = base + ([str(target)] if isinstance(target, Path) else list(map(str, target)))
    else:
        args = ["xdg-open", str(target if isinstance(target, Path) else target[0])]
    try:
        _view_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"  (couldn't open viewer: {e})")


# ---------- single-instance lock (watch mode) ----------
def acquire_lock(folder):
    lock = folder / ".preview-watcher.pid"
    try:
        if lock.exists():
            pid = int(lock.read_text().strip() or "0")
            if pid and Path(f"/proc/{pid}").exists():
                print(f"\n  A preview watcher is already running (pid {pid}).")
                print("  Drop images into the folder; they'll appear in its window. Exiting.\n")
                return False
    except Exception:
        pass
    folder.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))
    atexit.register(lambda: lock.unlink(missing_ok=True))
    return True


# ---------- modes ----------
def watch(folder):
    if not acquire_lock(folder):
        return
    outdir = folder / PREVIEW_DIR
    print(f"\n  CODEC screensaver preview — watching:\n    {folder}")
    print(f"  Previews open in ONE window ({_viewer()}); flip with Left/Right. Ctrl-C to stop.")
    print("  Best fit = TALL / portrait, high contrast.\n")
    if shutil.which("dolphin"):
        subprocess.Popen(["dolphin", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    render_folder(folder)
    if outdir.exists() and any(outdir.iterdir()):
        open_viewer(outdir)
    seen = {p: (p.stat().st_mtime, p.stat().st_size) for p in folder.iterdir() if is_input(p)}
    inotify = shutil.which("inotifywait")
    while True:
        try:
            if inotify:
                subprocess.run(["inotifywait", "-qq", "-t", "3", "-e", "close_write,create,moved_to", str(folder)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                time.sleep(1.0)
            current = {p: (p.stat().st_mtime, p.stat().st_size) for p in folder.iterdir() if is_input(p)}
            for p, sig in current.items():
                if seen.get(p) != sig:
                    time.sleep(0.15)
                    try:
                        render(p, outdir)
                        print(f"  + {p.name}", flush=True)
                    except Exception as e:
                        print(f"  ! {p.name}: {e}", flush=True)
            if current and (_view_proc is None or _view_proc.poll() is not None):
                open_viewer(outdir)                  # reopen if the user closed it
            seen = current
        except KeyboardInterrupt:
            print("\n  stopped.\n")
            return


def batch(folder, keep_watch):
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        print(f"  not a folder: {folder}")
        return
    print(f"\n  Rendering previews for images in:\n    {folder}\n")
    outdir, made = render_folder(folder)
    if not made:
        return
    print(f"\n  {len(made)} previews -> {outdir}")
    print(f"  Opening one window ({_viewer()}); flip with Left / Right arrows.\n")
    open_viewer(outdir)
    if keep_watch:
        # fall through to watching the same folder for more drops
        globals()["DROP"] = folder
        seen = {p: (p.stat().st_mtime, p.stat().st_size) for p in folder.iterdir() if is_input(p)}
        inotify = shutil.which("inotifywait")
        print("  Also watching this folder for new drops (Ctrl-C to stop).\n")
        while True:
            try:
                if inotify:
                    subprocess.run(["inotifywait", "-qq", "-t", "3", "-e", "close_write,create,moved_to", str(folder)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    time.sleep(1.0)
                current = {p: (p.stat().st_mtime, p.stat().st_size) for p in folder.iterdir() if is_input(p)}
                for p, sig in current.items():
                    if seen.get(p) != sig:
                        time.sleep(0.15)
                        render(p, outdir)
                        print(f"  + {p.name}", flush=True)
                seen = current
            except KeyboardInterrupt:
                print("\n  stopped.\n")
                return


def preview_files(paths):
    made = []
    for p in paths:
        p = p.expanduser().resolve()
        try:
            made.append(render(p, p.parent / PREVIEW_DIR))
            print(f"  ✓ {p.name}")
        except Exception as e:
            print(f"  ✗ {p.name}: {e}")
    if made:
        open_viewer(made)


def main():
    args = [a for a in sys.argv[1:] if a not in ("-w", "--watch")]
    keep_watch = any(a in ("-w", "--watch") for a in sys.argv[1:])
    if not args:
        watch(DROP)
    elif len(args) == 1 and Path(args[0]).expanduser().is_dir():
        batch(Path(args[0]), keep_watch)
    else:
        preview_files([Path(a) for a in args])


if __name__ == "__main__":
    main()
