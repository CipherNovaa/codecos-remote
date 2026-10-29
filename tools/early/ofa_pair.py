#!/usr/bin/env python3
"""ONE clean pairing attempt with the OFA remote via a pty-driven bluetoothctl.
pairable on -> agent -> scan until a NEW OFA device (not the app-config radio) -> pair -> wait.
Timestamped log; answers agent prompts permissively."""
import os, pty, re, select, sys, time
SKIP = {os.environ.get("OFA_ADDR", "").upper()}  # never pair the config radio
LOG = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.cache/ofa/pair.log")
SCAN_WAIT, PAIR_WAIT = 180, 60
ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x01|\x02|\r")
log = open(LOG, "w", buffering=1)
seen = []
def note(s):
    line = f"{time.strftime('%H:%M:%S')} {s}"; log.write(line + "\n"); print(line, flush=True)
    m = re.search(r"(?:NEW|CHG)\] Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2}) (.*)", s)
    if m and m.group(1) not in SKIP and ("OFA" in m.group(2) or m.group(1).startswith("8C:14:7D")):
        seen.append(m.group(1))
pid, fd = pty.fork()
if pid == 0: os.execvp("bluetoothctl", ["bluetoothctl"])
buf = ""
def send(c): note(f">>> {c}"); os.write(fd, (c + "\n").encode())
def pump(t, until=None):
    global buf
    end = time.time() + t
    while time.time() < end:
        if not select.select([fd], [], [], 0.2)[0]: continue
        try: buf += ansi.sub("", os.read(fd, 4096).decode(errors="replace"))
        except OSError: return False
        while "\n" in buf:
            line, buf = buf.split("\n", 1); line = line.strip()
            if not line: continue
            note(line)
            if until and re.search(until, line): return True
        if re.search(r"(Confirm passkey|Authorize service|Request confirmation|Accept pairing).*\(yes/no\)", buf, re.I):
            note(f"{buf.strip()} !!! AGENT PROMPT -> yes"); buf = ""; send("yes")
        elif re.search(r"Enter (PIN code|passkey)", buf, re.I):
            note(f"{buf.strip()} !!! AGENT PROMPT -> 000000"); buf = ""; send("000000")
    return False
pump(1.5)
for stale in (sys.argv[2:] if len(sys.argv) > 2 else []):   # only remove what the caller names
    send(f"remove {stale}"); pump(1)
send("power on"); pump(1); send("pairable on"); pump(1); send("default-agent"); pump(1)
send("scan le"); note(f"=== waiting up to {SCAN_WAIT}s for an OFA remote")
end = time.time() + SCAN_WAIT
while time.time() < end and not seen: pump(1)
MAC = seen[0] if seen else None
note(f"=== target MAC: {MAC}")
if not MAC: send("scan off"); pump(1); send("quit"); sys.exit(2)
pump(1); send("scan off"); pump(1.5); send(f"pair {MAC}")
pump(PAIR_WAIT, until=r"Pairing successful|Failed to pair")
ok = "Pairing successful" in open(LOG).read()
pump(5)
if not ok:
    note("=== PAIR FAILED — not trusting; removing the half-made entry")
    send(f"remove {MAC}"); pump(1); send("quit"); pump(1); sys.exit(3)
send(f"trust {MAC}"); pump(2); send(f"info {MAC}"); pump(3); send("quit"); pump(2); note("=== done")
