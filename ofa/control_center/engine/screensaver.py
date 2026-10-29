"""E-paper screensaver over BLE — reuses codec_screensaver's slot pipeline.

Faithful re-drive of ``codec_screensaver._live``'s set path (BEGIN / DATA* / COMMIT
with 0.6 s after the erase and 0.04 s between DATA frames, then verify reg(0) up to 5
times, re-sending DATA+COMMIT when the screen left the transfer open) — but with
on_progress / on_log callbacks instead of print(), so the GUI shows a live bar.

The packed 8072-byte blob is built off the paint thread (render executor). All the
lineup bookkeeping (which image is in which slot) delegates to codec_screensaver so the
CLI and the GUI share one lineup.json.
"""
import binascii

import codec_screensaver as ss

from . import render
from .link import BleSession

# re-exports so panels have one import for lineup + naming
load_lineup = ss.load_lineup
save_lineup = ss.save_lineup
record_cycle = ss.record_cycle
forget_slot = ss.forget_slot
slot_map_str = ss.slot_map_str


def _noop(*_a, **_k):
    pass


async def push_slot(source, slot, mode, method, invert=False,
                    on_progress=_noop, on_log=_noop, verify=True):
    """Write one image into a screensaver slot and verify it. Updates lineup.json.

    on_progress(done, total) fires per frame; on_log(str) narrates each step.
    Returns True if the slot verified PRESENT (or verify was skipped).
    """
    blob = await render._in_executor(ss.build_blob, str(source), mode, method, invert)
    present = None
    async with BleSession() as s:
        link = s.link
        on_log(f"main firmware: {await link.identify()}")
        seq = list(ss.set_frames(blob, slot, link.chunk))
        total = len(seq)
        for i, (name, frame) in enumerate(seq):
            await link.send(frame)
            on_progress(i + 1, total)
            on_log(f"{name} ok ({i + 1}/{total})")
            import asyncio
            await asyncio.sleep(0.6 if i == 0 else 0.04)

        if verify:
            on_log("verifying slot map on the device …")
            try:
                import asyncio
                for attempt in range(1, 6):
                    m = await link.reg(0)
                    present = bool(m >> slot & 1)
                    on_log(f"slot present = {ss.slot_map_str(m)} → slot {slot} "
                           f"{'PRESENT' if present else 'MISSING'}")
                    if present:
                        break
                    vals = {ss.REGS[j]: await link.reg(j) for j in ss.DIAG_REGS}
                    if vals.get("ext_version", await link.reg(16)) < 14:
                        on_log(f"(screen fw < v14: no forensic registers; last_err={vals.get('last_err')})")
                        break
                    data = [f for n, f in seq if n.startswith("DATA")]
                    on_log(ss.verdict(vals, binascii.crc32(blob) & 0xFFFFFFFF, len(data), len(blob)))
                    if attempt < 5 and vals["rx_active"] == 1 and vals["commit_diag"] & 0x10:
                        on_log(f"transfer still open: re-sending {len(data)} DATA + COMMIT "
                               f"[attempt {attempt + 1}/5] …")
                        for name, frame in seq[1:]:
                            await link.send(frame)
                            await asyncio.sleep(0.04)
                        continue
                    break
            except RuntimeError as e:
                on_log(f"(no readback in this screen firmware: {e}) — judge by the panel")
            try:
                await link.restore_version()
            except RuntimeError:
                pass

    # keep the local record of what is in the slot in sync (matches the CLI)
    ss.record_slot(slot, source, mode, method, invert)
    return present if present is not None else True


async def show(slot=None, on_log=_noop):
    async with BleSession() as s:
        await s.link.identify()
        await s.link.send(ss.show_frame(slot))
        on_log(f"shown slot {'(current)' if slot is None else slot}")


async def clear(slot, on_log=_noop):
    """slot=0xFF clears all. Also forgets the lineup record(s)."""
    async with BleSession() as s:
        await s.link.identify()
        await s.link.send(ss.clear_frame(slot))
    ss.forget_slot(slot)
    on_log(f"cleared slot {'ALL' if slot == 0xFF else slot}")


async def set_cycle(mode, on_log=_noop):
    async with BleSession() as s:
        await s.link.identify()
        await s.link.send(ss.mode_frame(mode))
    ss.record_cycle(mode)
    on_log(f"cycle mode = {mode}")


async def set_threshold(seconds, on_log=_noop):
    async with BleSession() as s:
        await s.link.identify()
        await s.link.send(ss.threshold_frame(int(seconds)))
    on_log(f"threshold = {seconds}s ({'disabled' if int(seconds) == 0 else 'enabled'})")


async def full_status():
    """The deep ~100-register status() dict (slower). For the diagnostics view."""
    async with BleSession() as s:
        await s.link.identify()
        return await s.link.status()
