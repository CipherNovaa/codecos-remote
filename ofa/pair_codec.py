#!/usr/bin/env python3
"""One pairing attempt for CODEC's exact MAC; never delete existing bonds."""
import ofa_identity
import argparse
import os
import pty
import re
import select
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    address = ofa_identity.hid_addr(1)
    ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x01|\x02|\r")
    log = args.log.open("x", buffering=1)
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp("bluetoothctl", ["bluetoothctl"])
    buffer = ""
    seen = False

    def note(line):
        stamp = time.strftime("%H:%M:%S")
        print(f"{stamp} {line}", flush=True)
        log.write(f"{stamp} {line}\n")

    def send(command):
        note(">>> " + command)
        os.write(fd, (command + "\n").encode())

    def pump(seconds, until=None):
        nonlocal buffer, seen
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            try:
                buffer += ansi.sub("", os.read(fd, 4096).decode(errors="replace"))
            except OSError:
                return False
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                if address in line:
                    seen = True
                # Avoid retaining unrelated nearby devices' names/addresses.
                if "Device " not in line or address in line:
                    note(line)
                if until and re.search(until, line):
                    return line
            if re.search(r"(Authorize service|Request confirmation|Accept pairing).*\(yes/no\)", buffer, re.I):
                if address in buffer:
                    note("Authorizing CODEC pairing prompt")
                    buffer = ""
                    send("yes")
                else:
                    note("Unidentified pairing prompt; refusing automatic acceptance")
                    buffer = ""
                    send("no")
        return False

    try:
        pump(1)
        for command in ("pairable on", "agent KeyboardDisplay", "default-agent"):
            send(command)
            pump(0.5)
        send("scan le")
        note("Waiting for CODEC only: select CODEC, then hold VOL+ and OK")
        deadline = time.monotonic() + 240
        while not seen and time.monotonic() < deadline:
            pump(1)
        if not seen:
            raise RuntimeError("CODEC did not advertise; no bond was changed")
        send("scan off")
        pump(1)
        send("pair " + address)
        result = pump(60, r"Pairing successful|Failed to pair")
        if not result or "Pairing successful" not in result:
            raise RuntimeError("Pairing not confirmed; preserving current bond state")
        send("trust " + address)
        pump(2)
        send("info " + address)
        pump(2)
        note("CODEC pairing succeeded")
    finally:
        send("scan off")
        send("quit")
        pump(1)
        os.close(fd)
        os.waitpid(pid, 0)
        log.close()


if __name__ == "__main__":
    main()
