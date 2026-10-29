#!/usr/bin/env python3
"""Beep the OFA remote via the standard BLE Immediate Alert Service (Find Me profile).
Discovered 2026-09-19 from an HCI snoop of the app's "Find My Remote": the app writes
the Alert Level characteristic (UUID 0x2a06) on the config/smart-control radio (…79:B0).
  0x02 = High Alert (beep) · 0x01 = Mild Alert · 0x00 = No Alert (stop)
Usage: ofa_beep.py [level]   (default 2; then run with 0 to stop, or it auto-stops after --secs)
"""
import asyncio, sys
from bleak import BleakClient

import os
ADDR = os.environ.get("OFA_ADDR", "XX:XX:XX:XX:XX:XX").upper()  # config radio (see ofa/ofa_identity.py)
ALERT = "00002a06-0000-1000-8000-00805f9b34fb"

async def main(level, secs):
    async with BleakClient(ADDR, timeout=25) as c:
        print(f"connected to {ADDR}")
        await c.write_gatt_char(ALERT, bytes([level]), response=False)
        print(f"wrote Alert Level = {level}" + (" (BEEP)" if level else " (stop)"))
        if level and secs > 0:
            await asyncio.sleep(secs)
            await c.write_gatt_char(ALERT, bytes([0]), response=False)
            print("auto-stopped (Alert Level = 0)")

level = int(sys.argv[1]) if len(sys.argv) > 1 else 2
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
asyncio.run(main(level, secs))
