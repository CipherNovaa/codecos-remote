#!/usr/bin/env python3
"""Receive IR with the Tiqiaa Tview (10c4:8468) and decode it.

Protocol from XenRE's reverse engineering (gitlab.com/XenRE/tiqiaa-usb-ir):
  USB bulk EP 0x01 out / 0x81 in, 61-byte fragments:
    [report_id][fragm_size][packet_idx][fragm_count][fragm_idx][payload...]
    host->dev report_id=2, dev->host report_id=1, payload <= 56 bytes/fragment.
  Reassembled packet: "ST" <cmd_id> <cmd_type> <body...> "EN"
    cmd types: L idle, S send, R recv mode, C cancel, O output(arm), D data, V version
  IR data (type D): each byte = bit7 level (1 = IR mark), bits0-6 = duration in 16us ticks.
  Receive: R (wait state 19) -> C -> O ; each capture ends with a D packet; send O to re-arm.

Run with sudo (or after installing the udev rule). Ctrl-C to stop.
Log: ~/.cache/ofa/tview-rx.log
"""
import sys, time, usb.core, usb.util

VID, PID = 0x10C4, 0x8468
TICK_US = 16
import os
LOG = os.path.expanduser("~/.cache/ofa/tview-rx.log")
out = open(LOG, "a", buffering=1)
def log(s):
    s = f"{time.strftime('%H:%M:%S')} {s}"; print(s, flush=True); out.write(s + "\n")

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None: sys.exit("Tview not found")
try:
    if dev.is_kernel_driver_active(0): dev.detach_kernel_driver(0)
except (usb.core.USBError, NotImplementedError): pass
dev.set_configuration()
usb.util.claim_interface(dev, 0)

pkt_idx = 0
cmd_id = 0
def next_id():
    global cmd_id
    cmd_id = cmd_id % 0x7F + 1
    return cmd_id

def send_packet(payload):
    global pkt_idx
    pkt_idx = pkt_idx % 15 + 1
    frags = [payload[i:i + 56] for i in range(0, len(payload), 56)]
    for n, f in enumerate(frags, 1):
        buf = bytes([2, len(f) + 3, pkt_idx, len(frags), n]) + f
        dev.write(0x01, buf, 1000)

def send_cmd(t):
    send_packet(b"ST" + bytes([next_id(), ord(t)]) + b"EN")

# --- reassembly ---
cur = None  # [pkt_idx, frag_count, last_idx, bytearray]
def read_packet(timeout):
    """Return (type, body) of the next complete packet, or None on timeout."""
    global cur
    end = time.time() + timeout / 1000
    while time.time() < end:
        try:
            fr = bytes(dev.read(0x81, 64, 200))
        except usb.core.USBTimeoutError:
            continue
        if len(fr) < 6 or fr[0] != 1 or fr[1] + 2 > len(fr): continue
        size, pidx, fcount, fidx = fr[1] + 2 - 5, fr[2], fr[3], fr[4]
        if cur and not (pidx == cur[0] and fcount == cur[1] and fidx == cur[2] + 1):
            cur = None
        if cur is None:
            if fcount > 0 and fidx == 1: cur = [pidx, fcount, 0, bytearray()]
            else: continue
        cur[2] = fidx
        cur[3] += fr[5:5 + size]
        if fidx == cur[1]:
            p = bytes(cur[3]); cur = None
            if len(p) > 6 and p[:2] == b"ST" and p[-2:] == b"EN":
                return chr(p[3]), p[4:-2]
    return None

def cmd_wait(t, timeout=800):
    send_cmd(t)
    end = time.time() + timeout / 1000
    while time.time() < end:
        r = read_packet(200)
        if r and r[0] == t: return r[1]
    return None

# --- decoding ---
def to_runs(data):
    runs = []
    for b in data:
        lvl, d = bool(b & 0x80), (b & 0x7F) * TICK_US
        if runs and runs[-1][0] == lvl: runs[-1][1] += d
        else: runs.append([lvl, d])
    while runs and not runs[0][0]: runs.pop(0)
    return runs

def demod(runs, gap=200):
    """The Tview's learning sensor reports the raw carrier (+32 -32 +32 ...). Merge marks
    separated by gaps shorter than `gap` us into one envelope mark."""
    env = []
    for lvl, d in runs:
        if not lvl and d < gap and env and env[-1][0]:
            env[-1][1] += d          # carrier gap: part of the mark
        elif env and env[-1][0] == lvl:
            env[-1][1] += d
        else:
            env.append([lvl, d])
    return env

def near(v, target, tol=0.35): return abs(v - target) <= target * tol

def decode(runs):
    # a capture can start mid-frame: try every mark as a potential header
    for i in range(0, len(runs) - 2):
        if runs[i][0]:
            r = decode_at(runs[i:])
            if r: return r
    return None

def decode_at(runs):
    if len(runs) < 3: return None
    m0, s0 = runs[0][1], runs[1][1] if len(runs) > 1 else 0
    # Sony SIRC: 2400 header mark, 600 spaces, mark 600=0 / 1200=1, LSB first
    if near(m0, 2400):
        bits = []
        for lvl, d in runs[2:]:
            if lvl:
                if near(d, 1200, .3): bits.append(1)
                elif near(d, 600, .45): bits.append(0)
                else: break
        if len(bits) in (12, 15, 20):
            cmd = sum(b << i for i, b in enumerate(bits[:7]))
            addr = sum(b << i for i, b in enumerate(bits[7:]))
            return f"SONY{len(bits)} addr=0x{addr:x} cmd=0x{cmd:02x} ({cmd})"
    # NEC: 9000 mark + 4500 space, bits: 560 mark + 560(0)/1690(1) space, LSB first
    if near(m0, 9000, .25) and near(s0, 4500, .3):
        bits = []
        for i in range(2, len(runs) - 1, 2):
            sp = runs[i + 1][1]
            if near(sp, 1690, .35): bits.append(1)
            elif near(sp, 560, .5): bits.append(0)
            else: break
        if len(bits) >= 32:
            v = [sum(bits[j * 8 + i] << i for i in range(8)) for j in range(4)]
            return f"NEC addr=0x{v[0]:02x}{'' if v[1] == v[0] ^ 0xFF else f'/{v[1]:02x}'} cmd=0x{v[2]:02x}"
    if near(m0, 9000, .25) and near(s0, 2250, .3): return "NEC repeat"
    return None

# --- main ---
ver = cmd_wait("V")
log(f"Tview opened (version reply {'ok' if ver is not None else 'none'})")
st = cmd_wait("R"); log(f"recv mode -> state {st[0] if st else '?'}")
cmd_wait("C")
send_cmd("O")
log("armed — point a remote at the dongle and press buttons")
try:
    while True:
        r = read_packet(1000)
        if not r:
            idle = globals().get("idle", 0) + 1; globals()["idle"] = idle
            if idle % 15 == 0:  # every ~15s re-arm, in case a capture timed out silently
                send_cmd("O"); log("(re-armed)")
            continue
        globals()["idle"] = 0
        t, body = r
        if t == "D":
            runs = demod(to_runs(body))
            open(os.path.expanduser("~/.cache/ofa/tview-raw.log"), "a").write(f"{time.time():.3f} {body.hex()}\n")
            name = decode(runs)
            raw = " ".join(f"{'+' if l else '-'}{d}" for l, d in runs[:24])
            log(f"IR {name or 'UNKNOWN'}   [{len(runs)} edges] {raw}{' …' if len(runs) > 24 else ''}")
            send_cmd("O")  # re-arm for the next capture
        else:
            log(f"(packet {t} {body.hex()})")
except KeyboardInterrupt:
    pass
finally:
    try: cmd_wait("L")
    except Exception: pass
    usb.util.release_interface(dev, 0)
