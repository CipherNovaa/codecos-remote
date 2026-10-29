#!/usr/bin/env python3
# OFA range logger: logs each keypress ARRIVAL + the link RSSI at that instant.
# Reuses ofa_keys.py's exclusive-grab reader (so stray keys can't hit the desktop)
# and adds an mgmt RSSI read (works once we're root). Run with sudo. Ctrl-C to stop.
#   sudo OFA_HID_ADDR=XX:XX:XX:XX:XX:XX python3 tools/radio/ofa_range.py [seconds]
import fcntl, glob, os, re, select, struct, socket, sys, time

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0

# ---------- key reader (from ofa_keys.py) ----------
EVIOCGRAB = 0x40044590
FMT = "llHHi"; SZ = struct.calcsize(FMT)
kn = {}
try:
    for m in re.finditer(r"#define\s+(KEY_\w+|BTN_\w+)\s+(0x[0-9a-fA-F]+|\d+)",
                         open("/usr/include/linux/input-event-codes.h").read()):
        kn.setdefault(int(m.group(2), 0), m.group(1))
except OSError: pass

def scan_ofa():
    found = {}
    for d in glob.glob("/sys/class/input/event*/device/name"):
        try: n = open(d).read().strip()
        except OSError: continue
        if n.startswith("OFA Pro Touch"):
            found["/dev/input/" + d.split("/")[4]] = n
    return found

# ---------- mgmt RSSI ----------
AF_BLUETOOTH = 31; BTPROTO_HCI = 1; HCI_DEV_NONE = 0xFFFF; HCI_CHANNEL_CONTROL = 3
MGMT_OP_GET_CONN_INFO = 0x0031; MGMT_EV_CMD_COMPLETE = 0x0001; MGMT_EV_CMD_STATUS = 0x0002
TARGET = os.environ.get("OFA_HID_ADDR", "XX:XX:XX:XX:XX:XX").upper()  # slot-1 HID identity (see ofa/ofa_identity.py)
def a2b(mac): return bytes(int(x, 16) for x in reversed(mac.split(":")))
target6 = a2b(TARGET); atypes = [1, 2, 0]

def open_mgmt():
    s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
    s.bind((HCI_DEV_NONE, HCI_CHANNEL_CONTROL)); return s

def mgmt_wait(s, want, timeout=0.3):
    end = time.time() + timeout
    while time.time() < end:
        s.settimeout(max(0.01, end - time.time()))
        try: data = s.recv(1024)
        except socket.timeout: return None, b""
        if len(data) < 6: continue
        ev, _i, plen = struct.unpack("<HHH", data[:6]); pay = data[6:6+plen]
        if ev in (MGMT_EV_CMD_COMPLETE, MGMT_EV_CMD_STATUS) and len(pay) >= 3:
            op, st = struct.unpack("<HB", pay[:3])
            if op == want: return st, pay[3:]
    return None, b""

def read_rssi(s):
    global atypes
    if not s: return None
    for at in list(atypes):
        try:
            s.send(struct.pack("<HHH", MGMT_OP_GET_CONN_INFO, 0, 7) + target6 + bytes([at]))
        except OSError: return None
        st, resp = mgmt_wait(s, MGMT_OP_GET_CONN_INFO)
        if st == 0 and len(resp) >= 10:
            atypes = [at]; return struct.unpack("b", resp[7:8])[0]
    return None

def log(s): print(f"{time.strftime('%H:%M:%S')} {s}", flush=True)

mgmt = None
try: mgmt = open_mgmt(); log("mgmt socket open (RSSI will attach to each keypress if root allows it)")
except Exception as e: log(f"(mgmt unavailable: {e} — keys still logged)")

fds = {}; t0 = time.time(); presses = 0
log("RANGE logger up — press keys from each spot; NOTE: the remote is GRABBED, so it won't drive the PC while this runs. Ctrl-C to stop.")
while time.time() - t0 < DUR:
    have = {p for p, _ in fds.values()}
    for p, n in scan_ofa().items():
        if p not in have:
            try:
                fd = os.open(p, os.O_RDONLY); fcntl.ioctl(fd, EVIOCGRAB, 1)
                fds[fd] = (p, n); log(f"+ link up ({p})")
            except OSError as e: log(f"! open {p}: {e}")
    r = select.select(list(fds), [], [], 1.0)[0] if fds else (time.sleep(1) or [])
    for fd in r:
        p, n = fds[fd]
        try: data = os.read(fd, SZ * 64)
        except OSError:
            log("- link dropped"); os.close(fd); del fds[fd]; continue
        for i in range(0, len(data), SZ):
            _, _, typ, code, val = struct.unpack(FMT, data[i:i+SZ])
            if typ == 1 and val == 1:  # key down
                presses += 1
                rv = read_rssi(mgmt)
                tag = f"RSSI {rv:>4} dBm" if rv is not None else "(rssi n/a)"
                log(f"KEY #{presses:<3} {kn.get(code, '?'):20} {tag}")
log(f"done -- {presses} keypresses logged")
