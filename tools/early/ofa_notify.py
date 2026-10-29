#!/usr/bin/env python3
"""Subscribe to the OFA remote's NON-HID notify characteristics (never subscribed in any
earlier test) and log every value they send. Re-subscribes on each reconnect. No root needed."""
import os, pty, re, select, time
D = "/org/bluez/hci0/dev_8C_14_7D_39_B0_7A"
CHARS = {f"{D}/service0053/char0056": "ATVV-audio ab5e0003",
         f"{D}/service0053/char0059": "ATVV-ctl   ab5e0004",
         f"{D}/service005c/char005f": "vendor     6487"}
LOG = os.path.expanduser("~/.cache/ofa/notify.log")
HID = os.environ.get("OFA_HID_ADDR", "XX:XX:XX:XX:XX:XX").upper()  # slot-1 HID identity (see ofa/ofa_identity.py)
ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x01|\x02|\r")
log = open(LOG, "a", buffering=1)
def note(s):
    s = f"{time.strftime('%H:%M:%S')} {s}"; log.write(s + "\n"); print(s, flush=True)
pid, fd = pty.fork()
if pid == 0: os.execvp("bluetoothctl", ["bluetoothctl"])
def send(c): os.write(fd, (c + "\n").encode()); time.sleep(0.4)
def subscribe():
    send("menu gatt")
    for path, name in CHARS.items():
        send(f"select-attribute {path}"); send("notify on")
    send("back"); note("=== subscribed to " + ", ".join(CHARS.values()))
note("notify listener started — wake the remote (OK) to connect")
buf = ""; last_value_from = None
# subscribe now if already connected
if "Connected: yes" in os.popen(f"bluetoothctl info {HID}").read(): subscribe()
while True:
    if not select.select([fd], [], [], 1.0)[0]: continue
    buf += ansi.sub("", os.read(fd, 8192).decode(errors="replace"))
    while "\n" in buf:
        line, buf = buf.split("\n", 1); line = line.strip()
        if not line: continue
        if "RSSI" in line or "ManufacturerData" in line: continue
        if re.search(rf"Device {HID} Connected: yes", line):
            note("+ remote connected"); time.sleep(1.5); subscribe()
        elif re.search(rf"Device {HID} Connected: no", line):
            note("- remote disconnected")
        elif re.search(r"Attribute .* Value:|Notify|Failed|Error|not permitted", line) or re.match(r"[0-9a-f]{2}( [0-9a-f]{2})+", line):
            for p, n in CHARS.items():
                if p in line: last_value_from = n
            note(f"[{last_value_from or '?'}] {line}")
