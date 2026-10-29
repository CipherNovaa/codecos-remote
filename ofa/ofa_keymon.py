#!/usr/bin/env python3
"""Hotplug-aware key monitor for the OFA remote.

The OFA remote reconnects PER KEYPRESS (a fresh uhid input device each time), so a
tool that grabs one device at startup misses everything. This refreshes the device
list continuously and reads any "OFA Pro Touch" device as it appears — NON-exclusive
(keys still reach the system). Prints keycode + name + X11/Qt nativeScanCode.

Run as root (reads /dev/input):
  sudo ofa/artifacts/venv/bin/python ofa/ofa_keymon.py
Ctrl-C to stop.
"""
import os
import select
import time
import evdev


def ofa_devices():
    out = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
            if "OFA" in d.name and evdev.ecodes.EV_KEY in d.capabilities():
                out.append(d)
        except Exception:
            pass
    return out


def main():
    print("Watching OFA remote keys (Ctrl-C to stop). Press/HOLD your remote buttons...", flush=True)
    print("  ↓ = press   ↑ = release (with hold time)   — a real firmware hold shows a"
          " long 'held' time; a tap-only key releases in ~0.0s.\n", flush=True)
    open_devs = {}
    last_refresh = 0.0
    press_t = {}  # e.code -> press timestamp (keyed by code; device is transient per-keypress)
    try:
        while True:
            now = time.time()
            if now - last_refresh > 0.15:
                for d in ofa_devices():
                    if d.path not in open_devs:
                        open_devs[d.path] = d
                last_refresh = now
            if not open_devs:
                time.sleep(0.05)
                continue
            fds = {d.fd: d for d in open_devs.values()}
            r, _, _ = select.select(list(fds), [], [], 0.15)
            for fd in r:
                d = fds[fd]
                try:
                    for e in d.read():
                        if e.type != evdev.ecodes.EV_KEY:
                            continue
                        name = evdev.ecodes.KEY.get(e.code, "?")
                        tag = os.path.basename(d.path)
                        if e.value == 1:  # key down
                            press_t[e.code] = time.time()
                            print(f"  ↓ evdev {e.code:<3} {name:<16} → X11/Qt scancode {e.code + 8}"
                                  f"   [{tag}]", flush=True)
                        elif e.value == 0:  # key up
                            held = time.time() - press_t.pop(e.code, time.time())
                            print(f"  ↑ evdev {e.code:<3} {name:<16}   held {held:5.2f}s"
                                  f"   [{tag}]", flush=True)
                        # e.value == 2 is kernel auto-repeat (per-keycode policy, not a firmware
                        # signal) — ignored so the hold time on ↑ stays the clean measure.
                except OSError:
                    open_devs.pop(d.path, None)  # device vanished (reconnect); refresh will re-add
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
