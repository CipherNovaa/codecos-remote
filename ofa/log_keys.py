#!/usr/bin/env python3
"""Exclusively capture OFA input devices, including reconnects. Run with sudo.

Ctrl-C releases the grab. Only devices named OFA Pro Touch are opened.
Never captures the desktop keyboard or other input devices.
"""
import argparse
import fcntl
import json
import os
import re
import select
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "llHHi"
SIZE = struct.calcsize(FORMAT)
GRAB = 0x40044590


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    names = {}
    header = Path("/usr/include/linux/input-event-codes.h").read_text()
    for name, code in re.findall(r"#define\s+(KEY_\w+|BTN_\w+)\s+(0x[0-9a-fA-F]+|\d+)", header):
        names.setdefault(int(code, 0), name)
    descriptors = {}
    with args.output.open("x", buffering=1) as output:
        os.chmod(args.output, 0o644)

        def log(event, **data):
            entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **data}
            text = json.dumps(entry)
            output.write(text + "\n")
            print(text, flush=True)

        log("waiting", instruction="Waiting for OFA only; press keys once attached+grabbed")
        try:
            while True:
                for path in Path("/sys/class/input").glob("event*/device/name"):
                    try:
                        name = path.read_text().strip()
                        device = "/dev/input/" + path.parts[-3]
                        if not name.startswith("OFA Pro Touch") or device in descriptors.values():
                            continue
                        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
                        try:
                            fcntl.ioctl(fd, GRAB, 1)
                        except OSError:
                            os.close(fd)
                            raise
                        descriptors[fd] = device
                        log("attached+grabbed", device=device, name=name)
                    except OSError as error:
                        log("attach_error", message=str(error))
                ready = select.select(list(descriptors), [], [], 0.5)[0]
                for fd in ready:
                    try:
                        data = os.read(fd, SIZE * 64)
                        if not data:
                            raise OSError("Input device closed")
                    except BlockingIOError:
                        continue
                    except OSError:
                        log("detached", device=descriptors.pop(fd))
                        os.close(fd)
                        continue
                    for offset in range(0, len(data), SIZE):
                        _, _, typ, code, value = struct.unpack_from(FORMAT, data, offset)
                        if typ == 4 and code == 4:
                            log("scan", device=descriptors[fd], scan=hex(value & 0xffffffff))
                        elif typ == 1:
                            log("key", device=descriptors[fd], code=code,
                                name=names.get(code, "unknown"), value=value)
        except KeyboardInterrupt:
            log("stopped")
        finally:
            for fd in descriptors:
                os.close(fd)


if __name__ == "__main__":
    main()
