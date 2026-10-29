#!/usr/bin/env python3
"""codec_screensaver — manage the OFA remote's e-paper screensaver over BLE (v15).

Design-A multi-slot feature (see docs/notes/SCREENSAVER-DESIGN.md and
SCREENSAVER-DEBUG-STATE.md): once the screen firmware is flashed, store up to 6
images and change them anytime over Bluetooth with NO reflash.  The main firmware
exposes opcode W 0C, which forwards each sub-op to the screen's a55a command 0x50.
Screen v13+ honours every sub-op below (v12 only knew slot 0).  v14 adds forensic
registers: after a MISSING verdict `set` reads them, prints WHY (transport / erase /
program / retention, with the flash address and driver code) and, when the firmware left
the transfer open (a crc mismatch), re-sends the DATA frames and retries COMMIT once.
v15 adds the wake-hardening registers 84..99 (`status` decodes them: rst_policy /
rst_sync = what the stock would have done at the last restore, wake_snap = the raw
wake state, quiet_after_wake, grace deferrals, dispatch waits).

    codec_screensaver set <image.png> [--slot N] [--mode cover|contain]
                                      [--method dither|threshold] [--show]
    codec_screensaver show [--slot N]            # blit a slot to the panel now (no slot: the current one / a pick)
    codec_screensaver clear [--slot N | --all]   # wipe a slot / all slots
    codec_screensaver cycle random|sequential    # how it picks among filled slots at each idle draw
    codec_screensaver threshold <seconds>        # 0 disables the saver, anything else enables it
    codec_screensaver lineup                     # show the recorded rotation (image names) vs what's on the device
    codec_screensaver apply                      # (re)load every image in the lineup file (e.g. after a reflash)
    codec_screensaver status                     # read back the firmware registers (real on-device state)
    codec_screensaver reg <idx>                  # read one register
    codec_screensaver chem alkaline|nimh         # battery gauge curve
    codec_screensaver restore-version            # put the real screen version (01 3f) back in main's cache
    codec_screensaver set <img> --slot 0 --dry-run   # build+print frames, no Bluetooth

READBACK.  With the INSTALLED main v1 (identity `SS01`) the screen answers a register
read by masquerading as the stock version reply: main caches its first two bytes at
0x20b166/7 and appends them as the LAST TWO BYTES of every `W 02` inventory reply.
`status` therefore does, per register: `W 0C 04 <idx> ff` then `W 02`, and checks that
byte -2 == 0x80|idx (proves the round trip) and takes byte -1 as the value.  It ends
with `restore-version`.  With main v2 (`SS02`) every cmd-0x50 reply is relayed as an
unsolicited notify `0c 03 <status> <payload>` and `status` reads the 20-byte STATUS
record directly.

Live prereqs (like the other config tools): phone Bluetooth OFF; select AMPLIFIER on
the remote to drop the HID link; hold ACTIVITIES+DEVICES; run via ofa/artifacts/venv.
"""
import ofa_identity
import argparse
import binascii
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screensaver_prep import fit_gray, one_bit, pack_1bpp, W, H  # noqa: E402

ADDR = ofa_identity.CONFIG_ADDR                                   # management/config radio
WRITE_UUID = "00001121-d102-11e1-9b23-00025b00a5a5"          # write-no-response
NOTIFY_UUID = "00001122-d102-11e1-9b23-00025b00a5a5"
PREFIX, OPCODE = 0x57, 0x0C                                  # 'W', screensaver opcode (W 0C)
OK, ERR, RELAY = bytes([OPCODE, 0x01]), bytes([OPCODE, 0x02]), bytes([OPCODE, 0x03])
PAYLOAD_LEN = 8 + (W + 7) // 8 * H                           # 8072: LVGL indexed-1bpp blob
NSLOTS = 6
MAX_DATA = 128                                               # main v1/v2 refuse a DATA frame with more payload bytes (0c 02)
THRESHOLD_MAX = 0xFDFF                                       # 0xFExx / 0xFFxx are the CHEM / REG aliases on the wire

# a55a command-0x50 sub-ops (carried verbatim in the W 0C payload)
BEGIN, DATA, COMMIT, SHOW, THRESHOLD, CLEAR, MODE, STATUS, REG, CHEM = range(10)
MODES = {"random": 0, "sequential": 1}
CHEMS = {"alkaline": 0, "nimh": 1}

EXT_VERSION = 15
REGS_BY_VERSION = {13: 61, 14: 84, 15: 100}                  # how many registers each screen firmware answers
REGS = ["slot_present", "cmd_calls", "sleep_hits", "sleep_draws", "show_blits", "restores", "act_calls",
        "act_mode", "shown_state", "target_state", "lp_mode", "rx_active", "last_err", "inits", "draw_target",
        "sentinel_V", "ext_version", "batt_pct", "bl_done", "flush_done", "armed", "mv_lo", "mv_hi", "variant",
        "act_caller", "restore_caller", "ignored_caller", "ignored", "fb_match", "ign_src", "rst_src", "skips",
        "busy_ms10", "busy_timeouts", "saver_active", "lptim_since_draw", "suppressed", "lptim_wakes", "scrubbed",
        "lptim_period_s", "busy_first", "busy_pre", "pre_ms10", "busy_fallbacks", "pa2_scrubbed", "rearms",
        "fb_lo", "fb_hi", "last_path", "panel_absent", "pa2_event", "defers",
        "cur_slot", "cycle_mode", "thr_lo", "thr_hi", "picks", "map_at_draw", "disabled", "rx_slot", "seed_lo",
        "rx_crc0", "rx_crc1", "rx_crc2", "rx_crc3", "rx_frames_lo", "rx_frames_hi", "rx_bytes_lo", "rx_bytes_hi",   # v14 forensics
        "err_addr0", "err_addr1", "err_addr2", "err_addr3", "err_code", "commit_diag", "slock_tries", "pad",
        "flash_ifr", "flash_cr", "slock2_hi", "slock3_lo", "slock3_b1", "slock3_b2", "slot_first_byte",
        "rst_pending", "grace_armed", "disp_calls", "disp_forced_full", "disp_busy_pre", "disp_ms10", "disp_timeouts",   # v15 wake hardening
        "grace_defers", "quiet_ms10", "wake_snap", "rst_policy", "rst_sync", "partial_cnt", "full_flag", "sync_skip", "grace_s"]
WAKE_BITS = {1: "uart-rx-edge", 2: "slept(0x872)", 4: "lptim-woke(0x873)", 8: "BUSY-high-at-wake", 16: "PA2-event", 32: "ARMED"}
ERR_CODES = {1: "programmer readback mismatch (byte did not take)", 6: "FLASH_CR.OP mode refused", 7: "erase driver error",
             8: "sector NOT blank after erase (protected/void sector, RM 10.6.1)", 9: "FLASH BUSY timeout",
             0xa: "SLOCK bit never set (unlock write ignored)", 0xfc: "Flash_Init failed", 0xfd: "erase range", 0xfe: "program range"}
STAGES = {0: "-", 1: "BEGIN (erase)", 2: "DATA (program)", 3: "COMMIT (header/present)"}
DIAG_REGS = [11, 12] + list(range(61, 84))


def slot_map_str(m):
    """6-bit presence map -> 'slots 0,2,5 [101001]' (bit i = slot i)."""
    filled = [i for i in range(NSLOTS) if m >> i & 1]
    bits = "".join("1" if m >> i & 1 else "." for i in range(NSLOTS))
    return f"{'slots ' + ','.join(map(str, filled)) if filled else 'none'} [{bits}]"
SRC = {0: "pa8-line", 1: "uart-rx-edge"}
CALLERS = {0: "-", 1: "first-run", 2: "uart-in-sleep", 3: "pa8/rx-edge flags", 4: "touch-INT(PA2)-in-sleep", 5: "other"}
VARIANTS = {1: "defer", 2: "sync", 3: "direct-blit+touch-rearm", 4: "direct-blit-NOREARM"}
PATHS = {0: "-", 1: "sleep blit", 2: "show blit", 3: "sleep skip (fb already held the image)"}


def wrap(sub):
    return bytes([PREFIX, OPCODE]) + bytes(sub)


# ---- lineup: a local record of which image (by path/mode/method) is in each slot ----
# The device only stores raw pixels, so it can't tell us an image's name. This file is the
# central, editable source of truth for the rotation; `set`/`setmany`/`clear`/`cycle` keep it
# in sync, `lineup` shows it against the device, and `apply` reloads the whole set (e.g. after a reflash).
# your image library + the named rotation record (override with OFA_SCREENSAVER_DIR)
LIBRARY_DIR = Path(os.environ.get("OFA_SCREENSAVER_DIR", Path.home() / "screensaver-finalists"))
LINEUP_PATH = LIBRARY_DIR / "lineup.json"


def load_lineup():
    try:
        return json.loads(LINEUP_PATH.read_text())
    except Exception:
        return {"slots": {}, "cycle": "random"}


def save_lineup(lu):
    try:
        LINEUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        LINEUP_PATH.write_text(json.dumps(lu, indent=2) + "\n")
    except OSError as e:
        print(f"  (could not save lineup file {LINEUP_PATH}: {e})", flush=True)


def record_slot(slot, path, mode, method, invert=False):
    lu = load_lineup()
    lu.setdefault("slots", {})[str(slot)] = {
        "path": str(Path(path).resolve()), "mode": mode, "method": method, "invert": bool(invert)}
    save_lineup(lu)


def forget_slot(slot):
    lu = load_lineup()
    lu["slots"] = {} if slot == 0xFF else {k: v for k, v in lu.get("slots", {}).items() if k != str(slot)}
    save_lineup(lu)


def record_cycle(mode):
    lu = load_lineup(); lu["cycle"] = mode; save_lineup(lu)


def print_lineup(lu, slot_map):
    print(f"  lineup file : {LINEUP_PATH}")
    print(f"  cycle       : {lu.get('cycle', 'random')}")
    slots = lu.get("slots", {})
    for i in range(NSLOTS):
        e = slots.get(str(i))
        dev = "?" if slot_map is None else ("PRESENT" if (slot_map >> i) & 1 else "empty")
        if e:
            print(f"  slot {i}: {Path(e['path']).name:28} ({e.get('mode','cover')}/{e.get('method','threshold')})   [device: {dev}]")
        else:
            print(f"  slot {i}: {'(not recorded)':28}    [device: {dev}]")


def build_blob(image, mode, method, invert):
    blob = pack_1bpp(one_bit(fit_gray(image, mode), method, invert))
    if len(blob) != PAYLOAD_LEN:
        raise ValueError(f"blob is {len(blob)} bytes, expected {PAYLOAD_LEN}")
    return blob


def set_frames(blob, slot, chunk):
    """The W 0C frame sequence to write one slot: BEGIN, DATA*, COMMIT."""
    yield "BEGIN", wrap([BEGIN, slot]) + len(blob).to_bytes(2, "little")
    for off in range(0, len(blob), chunk):
        yield f"DATA @{off}", wrap([DATA]) + off.to_bytes(2, "little") + blob[off:off + chunk]
    crc = binascii.crc32(blob) & 0xFFFFFFFF
    yield "COMMIT", wrap([COMMIT]) + crc.to_bytes(4, "little")


def show_frame(slot=None): return wrap([SHOW] + ([slot & 0xFF] if slot is not None else []))


def threshold_frame(s):
    s = int(s)
    if not 0 <= s <= THRESHOLD_MAX:
        raise ValueError(f"threshold must be 0..{THRESHOLD_MAX} seconds (0xFExx/0xFFxx are register aliases)")
    return wrap([THRESHOLD]) + s.to_bytes(2, "little")
def clear_frame(slot):   return wrap([CLEAR, slot & 0xFF])
def mode_frame(m):       return wrap([MODE, MODES[m]])
def status_frame():      return wrap([STATUS])
def reg_alias_frame(i):  return wrap([THRESHOLD, i & 0xFF, 0xFF])          # main v1: THRESHOLD 0xFFii == REG ii
def chem_alias_frame(c): return wrap([THRESHOLD, c & 0xFF, 0xFE])          # main v1: THRESHOLD 0xFE0c == CHEM c
def reg_frame(i):        return wrap([REG, i & 0xFF])                       # main v2 native
def chem_frame(c):       return wrap([CHEM, c & 0xFF])
def identity_frame():    return wrap([])


def decode_status(rec):
    if len(rec) < 16 or rec[:2] != b"S7":
        raise ValueError(f"unexpected STATUS record: {rec.hex(' ')}")
    return {REGS[i]: rec[2 + i] for i in range(14)}


def dry_run(blob, slot, chunk):
    seq = list(set_frames(blob, slot, chunk))
    data = [f for f in seq if f[0].startswith("DATA")]
    print(f"slot          : {slot}")
    print(f"image payload : {len(blob)} bytes  crc32={binascii.crc32(blob) & 0xFFFFFFFF:#010x}")
    print(f"chunk size    : {chunk} data bytes/frame  ->  {len(data)} DATA frames")
    print(f"frame sequence: BEGIN, {len(data)}x DATA, COMMIT   ({len(seq)} writes)")
    for name, f in seq[:2] + [("...", b"")] + seq[-1:]:
        print(f"  {name:12} {f.hex(' ') if f else ''}"[:96])
    print(f"  SHOW         {show_frame().hex(' ')}   (SHOW slot {slot}: {show_frame(slot).hex(' ')})")
    print(f"  CLEAR {slot}      {clear_frame(slot).hex(' ')}   CLEAR all: {clear_frame(0xFF).hex(' ')}")
    print(f"  CYCLE random {mode_frame('random').hex(' ')}   sequential: {mode_frame('sequential').hex(' ')}")
    print(f"  THRESHOLD 0  {threshold_frame(0).hex(' ')} (saver off)   300: {threshold_frame(300).hex(' ')} (on)")
    print(f"  REG 15 (v1)  {reg_alias_frame(15).hex(' ')}   then W 02 -> inventory[-2:] == 8f 56")
    print(f"  REG 15 (v2)  {reg_frame(15).hex(' ')}   -> notify 0c 03 00 8f 56 ...")


def decode_forensics(v):
    """v14 registers 61..82 -> readable fields (all keys present even on older firmware = 0)."""
    g = lambda k: v.get(k, 0)
    rx_state = g("rx_crc0") | g("rx_crc1") << 8 | g("rx_crc2") << 16 | g("rx_crc3") << 24
    out = {
        "rx_frames": g("rx_frames_lo") | g("rx_frames_hi") << 8,
        "rx_bytes": g("rx_bytes_lo") | g("rx_bytes_hi") << 8,
        "rx_crc": f"{(~rx_state) & 0xFFFFFFFF:#010x}",
        "err_addr": f"{g('err_addr0') | g('err_addr1') << 8 | g('err_addr2') << 16 | g('err_addr3') << 24:#x}",
        "err_code": f"{g('err_code')} ({ERR_CODES.get(g('err_code'), '-')})",
        "commit_diag": (f"{g('commit_diag'):#04x} (received {'OK' if g('commit_diag') & 1 else 'BAD'}, "
                        f"flash {'OK' if g('commit_diag') & 2 else 'BAD'})") if g("commit_diag") & 0x10 else "no COMMIT since BEGIN",
        "slock_tries": g("slock_tries"),
        "flash_ifr": f"{g('flash_ifr'):#04x}{' IF1=PROTECTION ALARM' if g('flash_ifr') & 2 else ''}",
        "slock2_hi": f"{g('slock2_hi'):#04x} (slot 0 needs bits 28..31 -> 0xf0 nibble)",
        "slock3": f"{g('slock3_lo') | g('slock3_b1') << 8 | g('slock3_b2') << 16:#08x} (slot1 bits0-3, slot2 4-7, slot3 8-11, slot4 12-15, slot5 16-19)",
        "flash_cr": f"{g('flash_cr'):#04x} (OP {g('flash_cr') & 3}{', BUSY' if g('flash_cr') & 0x10 else ''})",
        "slot_first_byte": f"{g('slot_first_byte'):#04x}",
    }
    return out


def decode_wake(v):
    """v15 registers 84..99 -> readable fields."""
    snap = v["wake_snap"]
    bits = [n for b, n in WAKE_BITS.items() if snap & b] or ["-"]
    return {
        "wake_snap": f"{snap:#04x} [{', '.join(bits)}; pre-wake mode {snap >> 6}]",
        "rst_policy": ("FULL (stock chose a full update for the restored page)" if v["rst_policy"]
                       else "PARTIAL (stock would have used the partial LUT -- the v14 freeze mode; v15 forced FULL)") if v["restores"] else "- (no restore yet)",
        "rst_sync": ("async (stock 0x1858 returns without waiting)" if v["rst_sync"]
                     else "SYNC page group (stock would wait <= 1.5 s then RST; v15 bypassed that wait)") if v["restores"] else "-",
        "restore_pending": v["rst_pending"],
        "grace": f"{'ARMED' if v['grace_armed'] else 'idle'} ({v['grace_s']} s of awake time after a restore; {v['grace_defers']} sleep decisions deferred so far)",
        "quiet_after_wake": (f"~{v['quiet_ms10'] * 8} ms of awake time from the last restore to the first sleep decision"
                             if v["quiet_ms10"] else "- (no sleep decision since the last restore)"),
        "dispatches": f"{v['disp_calls']} page builds, {v['disp_forced_full']} restore builds forced FULL",
        "dispatch_wait": (f"BUSY was {'HIGH' if v['disp_busy_pre'] else 'low'} at the last page build; last wait ~{v['disp_ms10'] * 8} ms; "
                          f"{v['disp_timeouts']} capped (4 s)"),
        "refresh_live": f"partial_cnt {v['partial_cnt']} (>= 30 -> next flush FULL), full_flag {v['full_flag']}, sync_skip {v['sync_skip']}",
    }


def verdict(v, expect_crc, expect_frames, expect_bytes):
    """Name the failure from the forensic registers after a MISSING slot."""
    f = decode_forensics(v)
    st = v.get("last_err", 0); diag = v.get("commit_diag", 0)
    if st in (1, 2, 3):
        return f"{STAGES[st]} FAILED at {f['err_addr']}: code {f['err_code']}; IFR {f['flash_ifr']}; SLOCK2hi {f['slock2_hi']}; SLOCK3 {f['slock3']}"
    if not diag & 0x10:
        return "no COMMIT reached the screen (BEGIN/DATA/COMMIT frames dropped in transport, or the screen reset) -- nothing programmed"
    rx_ok, fl_ok = diag & 1, diag & 2
    if rx_ok and not fl_ok:
        return (f"RETENTION: all {f['rx_frames']} frames arrived (rx crc {f['rx_crc']} == expected) and every byte read back OK when "
                f"programmed, but the flash crc is wrong at COMMIT -> the die is not holding data at this address")
    if not rx_ok:
        return (f"TRANSPORT: the screen programmed {f['rx_frames']}/{expect_frames} DATA frames, {f['rx_bytes']}/{expect_bytes} bytes "
                f"(rx crc {f['rx_crc']} vs expected {expect_crc:#010x}) -> frames lost between the tool and ss_command; "
                f"flash {'also' if not fl_ok else 'unexpectedly'} {'wrong' if not fl_ok else 'OK'}")
    return "COMMIT crcs both OK but the header/present step failed without a stage code (unexpected)"


class Link:
    def __init__(self, client, queue, chunk):
        self.client, self.queue, self.chunk = client, queue, chunk
        self.main_version = None

    async def write(self, frame):
        import asyncio
        await asyncio.wait_for(self.client.write_gatt_char(WRITE_UUID, frame, response=False), timeout=15)

    async def read(self, timeout=8):
        import asyncio
        return await asyncio.wait_for(self.queue.get(), timeout=timeout)

    async def drain(self):
        while not self.queue.empty():
            self.queue.get_nowait()

    async def send(self, frame):
        """W 0C frame -> main's immediate `0c 01` (forwarded) or `0c 02` (refused)."""
        await self.drain()
        await self.write(frame)
        while True:
            reply = await self.read()
            if reply[:2] == RELAY:
                continue                                  # a late relay from an earlier command
            if reply[:2] != OK:
                raise RuntimeError(f"device error: {reply.hex(' ')}")
            return reply

    async def identify(self):
        r = await self.send(identity_frame())
        self.main_version = r[2:6].decode(errors="replace")
        return self.main_version

    async def relay(self, timeout=3):
        """main v2: wait for the screen's relayed reply `0c 03 <status> <payload>`."""
        import asyncio
        while True:
            r = await self.read(timeout)
            if r[:2] == RELAY:
                if r[2] != 0:
                    raise RuntimeError(f"screen NAK (status {r[2]:#x}): {r.hex(' ')}")
                return r[3:]

    async def inventory_tail(self):
        """`W 02` -> 512-byte inventory (chunked notifies); the last two bytes are the
        screen version cache (0x20b166/7) that the masquerade overwrites."""
        import asyncio
        await self.drain()
        await self.write(bytes([PREFIX, 0x02]))
        blob = b""
        try:
            while len(blob) < 512:
                blob += await self.read(2)
        except asyncio.TimeoutError:
            pass
        if len(blob) != 512 or blob[0] != 0x02:
            raise RuntimeError(f"unexpected W 02 reply ({len(blob)} bytes)")
        return blob[-2], blob[-1]

    async def reg(self, idx):
        import asyncio
        if self.main_version == "SS02":
            await self.send(reg_frame(idx))
            p = await self.relay()
            if len(p) < 2 or p[0] != (0x80 | idx):
                raise RuntimeError(f"bad REG relay: {p.hex(' ')}")
            return p[1]
        await self.send(reg_alias_frame(idx))
        await asyncio.sleep(0.3)                           # wake preamble + 35 ms transmit + screen reply + main cache
        tag, val = await self.inventory_tail()
        if tag != (0x80 | idx):                            # one retry: the reply may have raced the first W 02
            await asyncio.sleep(0.5)
            tag, val = await self.inventory_tail()
        if tag != (0x80 | idx):
            raise RuntimeError(f"REG {idx}: main cache holds {tag:02x} {val:02x} (expected tag {0x80 | idx:02x}); "
                               "did cmd 0x50 reach the screen?")
        return val

    async def restore_version(self):
        import asyncio
        await self.send(reg_frame(0xFF) if self.main_version == "SS02" else reg_alias_frame(0xFF))
        if self.main_version == "SS02":
            await self.relay()
        else:
            await asyncio.sleep(0.3)
            tag, val = await self.inventory_tail()
            if (tag, val) != (0x01, 0x3F):
                await asyncio.sleep(0.5)
                tag, val = await self.inventory_tail()
            if (tag, val) != (0x01, 0x3F):
                raise RuntimeError(f"version cache is {tag:02x} {val:02x} after restore")

    async def status(self):
        import asyncio
        if self.main_version == "SS02":
            await self.send(status_frame())
            return decode_status(await self.relay())
        vals = {}
        ver = await self.reg(16)                           # v13 firmware has 61 registers, v14 84, v15 100
        for idx, name in enumerate(REGS[:REGS_BY_VERSION.get(ver, 61)]):
            vals[name] = await self.reg(idx)
        try:
            await self.restore_version()
        except RuntimeError:
            pass
        if vals["sentinel_V"] != 0x56 or vals["ext_version"] not in REGS_BY_VERSION:
            raise RuntimeError(f"register sentinel mismatch (expected ext_version {EXT_VERSION}): {vals}")
        if vals["ext_version"] != EXT_VERSION:
            print(f"  (screen firmware is v{vals['ext_version']}; this tool is for v{EXT_VERSION} -- newer registers unavailable)")
        out = dict(vals)
        out["shown_state"] = hex(vals["shown_state"]); out["target_state"] = hex(vals["target_state"])
        out["draw_target"] = hex(vals["draw_target"]); out["batt_mv"] = vals["mv_lo"] | vals["mv_hi"] << 8
        out["variant"] = VARIANTS.get(vals["variant"], vals["variant"])
        for k in ("act_caller", "restore_caller", "ignored_caller"):
            out[k] = CALLERS.get(vals[k], vals[k])
        for k in ("ign_src", "rst_src"):
            out[k] = SRC.get(vals[k], vals[k])
        out["last_path"] = PATHS.get(vals["last_path"], vals["last_path"])
        out["fb_bytes_written"] = vals["fb_lo"] | vals["fb_hi"] << 8      # 8064 after any blit
        out["slot_present"] = slot_map_str(vals["slot_present"])
        out["map_at_draw"] = slot_map_str(vals["map_at_draw"])
        out["cur_slot"] = "none" if vals["cur_slot"] == 0xFF else vals["cur_slot"]
        out["cycle_mode"] = {0: "random", 1: "sequential"}.get(vals["cycle_mode"], vals["cycle_mode"])
        out["threshold"] = vals["thr_lo"] | vals["thr_hi"] << 8
        out["saver"] = "DISABLED (threshold 0)" if vals["disabled"] else "enabled"
        out.update(decode_forensics(vals))
        if "wake_snap" in vals:                            # v15 wake hardening
            out.update(decode_wake(vals))
        for k in ("mv_lo", "mv_hi", "fb_lo", "fb_hi", "thr_lo", "thr_hi", "disabled", "pad", "rx_crc0", "rx_crc1", "rx_crc2", "rx_crc3",
                  "rx_frames_lo", "rx_frames_hi", "rx_bytes_lo", "rx_bytes_hi", "err_addr0", "err_addr1", "err_addr2", "err_addr3",
                  "quiet_ms10", "disp_ms10"):
            out.pop(k, None)
        return out


async def _live(steps, chunk_hint, verify=True):
    import asyncio
    from bleak import BleakClient
    got = asyncio.Queue()
    async with BleakClient(ADDR, timeout=25) as client:
        await client.start_notify(NOTIFY_UUID, lambda _c, d: got.put_nowait(bytes(d)))
        mtu = getattr(client, "mtu_size", 23) or 23
        chunk = max(16, min(chunk_hint, MAX_DATA, mtu - 3 - 5))   # ATT(3) + W0C header(op+sub+off = up to 5); main caps DATA at 128
        link = Link(client, got, chunk)
        print(f"  main firmware: {await link.identify()}", flush=True)

        for kind, arg in steps:
            if kind == "set":
                blob, slot = arg
                seq = list(set_frames(blob, slot, chunk))
                for i, (name, frame) in enumerate(seq):
                    await link.send(frame)
                    print(f"  {name} ok ({i + 1}/{len(seq)})", flush=True)
                    # BEGIN erases an 8 KiB slot on the screen; let it finish before
                    # streaming data, and keep a small gap so its UART never overruns.
                    await asyncio.sleep(0.6 if i == 0 else 0.04)
                if verify:
                    print("  verifying slot map on the device ...", flush=True)
                    try:
                        for attempt in range(1, 6):
                            m = await link.reg(0)
                            ok = m >> slot & 1
                            print(f"  slot present = {slot_map_str(m)} -> slot {slot} {'PRESENT' if ok else 'MISSING'}")
                            if ok:
                                break
                            # v14 forensics: say WHY, then re-send once if the firmware left the transfer open
                            vals = {REGS[i]: await link.reg(i) for i in DIAG_REGS}
                            if vals.get("ext_version", await link.reg(16)) < 14:
                                print("  (screen firmware < v14: no forensic registers; last_err =", vals["last_err"], ")")
                                break
                            data = [f for n, f in seq if n.startswith("DATA")]
                            print("  " + verdict(vals, binascii.crc32(blob) & 0xFFFFFFFF, len(data), len(blob)), flush=True)
                            for k, val in decode_forensics(vals).items():
                                print(f"    {k:15}: {val}")
                            if attempt < 5 and vals["rx_active"] == 1 and vals["commit_diag"] & 0x10:
                                print(f"  transfer still open on the screen: re-sending {len(data)} DATA frames + COMMIT (no new erase) [attempt {attempt + 1}/5] ...", flush=True)
                                for i, (name, frame) in enumerate(seq[1:]):
                                    await link.send(frame)
                                    await asyncio.sleep(0.04)
                                print("  re-sent; verifying again ...", flush=True)
                                continue
                            break
                    except RuntimeError as e:
                        print(f"  (no readback in this screen firmware: {e}) -- judge by the panel", flush=True)
                    try:
                        await link.restore_version()
                    except RuntimeError:
                        pass
            elif kind == "show":
                await link.send(show_frame(arg))
                print(f"  shown slot {'(current / pick)' if arg is None else arg} (blitted straight to the panel; the next page change overwrites it)", flush=True)
            elif kind == "threshold":
                await link.send(threshold_frame(arg)); print(f"  threshold = {arg} ({'disabled' if arg == 0 else 'enabled'})", flush=True)
            elif kind == "clear":
                await link.send(clear_frame(arg)); print(f"  cleared slot {arg if arg != 0xFF else 'ALL'}", flush=True)
            elif kind == "cycle":
                await link.send(mode_frame(arg)); print(f"  cycle mode = {arg}", flush=True)
            elif kind == "chem":
                await link.send(chem_frame(arg) if link.main_version == "SS02" else chem_alias_frame(arg))
                print(f"  battery curve = {'nimh' if arg else 'alkaline (stock)'}", flush=True)
            elif kind == "reg":
                v = await link.reg(arg)
                print(f"  reg {arg} ({REGS[arg] if arg < len(REGS) else '?'}) = {v} ({v:#04x})", flush=True)
                try:
                    await link.restore_version()
                except RuntimeError:
                    pass
            elif kind == "restore":
                await link.restore_version(); print("  version cache restored to 01 3f", flush=True)
            elif kind == "status":
                st = await link.status()
                for k, v in st.items():
                    print(f"  {k:13}: {v}")
            elif kind == "lineup":
                m = None
                try:
                    m = await link.reg(0)
                    await link.restore_version()
                except RuntimeError:
                    pass
                print_lineup(load_lineup(), m)
    print("done.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set"); s.add_argument("image", type=Path)
    s.add_argument("--slot", type=int, default=0, choices=range(NSLOTS))
    s.add_argument("--mode", choices=["cover", "contain"], default="cover")
    s.add_argument("--method", choices=["dither", "threshold"], default="threshold")
    s.add_argument("--invert", action="store_true")
    s.add_argument("--show", action="store_true", help="also display it immediately")
    s.add_argument("--dry-run", action="store_true", help="build+print frames, no Bluetooth")
    s.add_argument("--chunk", type=int, default=MAX_DATA, help=f"data bytes per DATA frame (main caps at {MAX_DATA})")
    s.add_argument("--no-verify", action="store_true", help="skip the register readback (v6 screen firmware has none)")
    sm = sub.add_parser("setmany", help="load several images into consecutive slots in ONE BLE session")
    sm.add_argument("images", nargs="+", type=Path)
    sm.add_argument("--start", type=int, default=0, choices=range(NSLOTS), help="first slot to fill (default 0)")
    sm.add_argument("--mode", choices=["cover", "contain"], default="cover")
    sm.add_argument("--method", choices=["dither", "threshold"], default="threshold")
    sm.add_argument("--invert", action="store_true")
    sm.add_argument("--cycle", choices=list(MODES), help="also set the cycle mode after loading")
    sh = sub.add_parser("show"); sh.add_argument("--slot", type=int, choices=range(NSLOTS), help="blit this slot (default: current / a pick)")
    t = sub.add_parser("threshold"); t.add_argument("seconds", type=int)
    cy = sub.add_parser("cycle"); cy.add_argument("mode", choices=list(MODES))
    cl = sub.add_parser("clear")
    cl.add_argument("--slot", type=int, choices=range(NSLOTS)); cl.add_argument("--all", action="store_true")
    ch = sub.add_parser("chem"); ch.add_argument("chemistry", choices=list(CHEMS))
    rg = sub.add_parser("reg"); rg.add_argument("idx", type=int)
    sub.add_parser("restore-version")
    sub.add_parser("status")
    sub.add_parser("lineup", help="show the recorded slot lineup (image names) vs what's actually on the device")
    sub.add_parser("apply", help="(re)load every image recorded in the lineup file onto the device (e.g. after a reflash)")
    a = p.parse_args()

    if a.cmd == "set":
        blob = build_blob(a.image, a.mode, a.method, a.invert)
        if a.dry_run:
            return dry_run(blob, a.slot, a.chunk)
        steps = [("set", (blob, a.slot))] + ([("show", a.slot)] if a.show else [])
        record_slot(a.slot, a.image, a.mode, a.method, a.invert)
    elif a.cmd == "setmany":
        if a.start + len(a.images) > NSLOTS:
            p.error(f"{len(a.images)} images starting at slot {a.start} exceeds {NSLOTS} slots")
        steps = [("set", (build_blob(img, a.mode, a.method, a.invert), a.start + i)) for i, img in enumerate(a.images)]
        for i, img in enumerate(a.images):
            record_slot(a.start + i, img, a.mode, a.method, a.invert)
        if a.cycle:
            steps.append(("cycle", a.cycle)); record_cycle(a.cycle)
    elif a.cmd == "apply":
        lu = load_lineup(); slots = lu.get("slots", {})
        if not slots:
            p.error(f"no lineup recorded in {LINEUP_PATH}; load images with set/setmany first")
        steps = [("set", (build_blob(Path(slots[sl]["path"]), slots[sl].get("mode", "cover"),
                                     slots[sl].get("method", "threshold"), slots[sl].get("invert", False)), int(sl)))
                 for sl in sorted(slots, key=int)]
        if lu.get("cycle"):
            steps.append(("cycle", lu["cycle"]))
    elif a.cmd == "show":
        steps = [("show", a.slot)]
    elif a.cmd == "threshold":
        steps = [("threshold", a.seconds)]
    elif a.cmd == "cycle":
        steps = [("cycle", a.mode)]; record_cycle(a.mode)
    elif a.cmd == "chem":
        steps = [("chem", CHEMS[a.chemistry])]
    elif a.cmd == "reg":
        steps = [("reg", a.idx)]
    elif a.cmd == "restore-version":
        steps = [("restore", None)]
    elif a.cmd == "lineup":
        steps = [("lineup", None)]
    elif a.cmd == "clear":
        if not a.all and a.slot is None:
            p.error("clear needs --slot N or --all")
        steps = [("clear", 0xFF if a.all else a.slot)]
        forget_slot(0xFF if a.all else a.slot)
    else:
        steps = [("status", None)]

    import asyncio
    asyncio.run(_live(steps, getattr(a, "chunk", 192), verify=not getattr(a, "no_verify", False)))


if __name__ == "__main__":
    main()
