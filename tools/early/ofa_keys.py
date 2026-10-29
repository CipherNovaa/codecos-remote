#!/usr/bin/env python3
# Log key events from the OFA remote (run with sudo). GRABS the remote exclusively while
# running, so no key reaches logind/compositor (the Power key shut the PC down once).
# Survives disconnect/reconnect. Ctrl-C to stop. Log: ~/.cache/ofa/ofa-keys.log
import fcntl, glob, os, re, select, struct, time
EVIOCGRAB = 0x40044590
FMT = "llHHi"; SZ = struct.calcsize(FMT)
LOG = os.path.expanduser("~/.cache/ofa/ofa-keys.log")
kn = {}
try:
    for m in re.finditer(r"#define\s+(KEY_\w+|BTN_\w+)\s+(0x[0-9a-fA-F]+|\d+)", open("/usr/include/linux/input-event-codes.h").read()):
        kn.setdefault(int(m.group(2), 0), m.group(1))
except OSError: pass
out = open(LOG, "a", buffering=1); os.chmod(LOG, 0o644)
def log(s):
    s = f"{time.strftime('%H:%M:%S')} {s}"; print(s, flush=True); out.write(s + "\n")
def scan():
    found = {}
    for d in glob.glob("/sys/class/input/event*/device/name"):
        n = open(d).read().strip()
        if n.startswith("OFA Pro Touch"):
            found["/dev/input/" + d.split("/")[4]] = n
    return found
fds = {}
log("logger started (exclusive grab) — waiting for the remote")
while True:
    have = {p for p, _ in fds.values()}
    for p, n in scan().items():
        if p not in have:
            try:
                fd = os.open(p, os.O_RDONLY); fcntl.ioctl(fd, EVIOCGRAB, 1)
                fds[fd] = (p, n); log(f"+ attached+grabbed {n} ({p})")
            except OSError as e: log(f"! open {p}: {e}")
    r = select.select(list(fds), [], [], 1.0)[0] if fds else (time.sleep(1) or [])
    for fd in r:
        p, n = fds[fd]
        try: data = os.read(fd, SZ * 64)
        except OSError:
            log(f"- detached {n}"); os.close(fd); del fds[fd]; continue
        for i in range(0, len(data), SZ):
            _, _, typ, code, val = struct.unpack(FMT, data[i:i+SZ])
            if typ == 1 and val in (1, 2):
                log(f"{n:32} {code:4d} {kn.get(code, '?'):22} {'down' if val == 1 else 'repeat'}")
            elif typ == 4 and code == 4:
                log(f"{n:32}      scan=0x{val & 0xffffffff:x}")
