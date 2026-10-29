#!/usr/bin/env python3
"""Live BLE connection RSSI for the OFA remote via the BlueZ mgmt socket.
The 'get connections' call was broken on the author's machine, so we poll 'Get Connection
Information' DIRECTLY on the target, fast, to catch the remote's per-keypress
connect windows. HOLD a key down to keep it connected for a steady reading.
Usage: rssi_logger.py [seconds] [MAC]
"""
import os, socket, struct, sys, time, datetime

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
HCI_DEV_NONE = 0xFFFF
HCI_CHANNEL_CONTROL = 3

MGMT_OP_GET_CONN_INFO = 0x0031
MGMT_EV_CMD_COMPLETE  = 0x0001
MGMT_EV_CMD_STATUS    = 0x0002
INDEX = 0

DUR    = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
TARGET = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("OFA_HID_ADDR", "XX:XX:XX:XX:XX:XX")).upper()

def a2b(mac): return bytes(int(x, 16) for x in reversed(mac.split(":")))

def open_mgmt():
    s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
    s.bind((HCI_DEV_NONE, HCI_CHANNEL_CONTROL))
    return s

def send(s, opcode, params=b""):
    s.send(struct.pack("<HHH", opcode, INDEX, len(params)) + params)

def wait(s, want, timeout=0.4):
    end = time.time() + timeout
    while time.time() < end:
        s.settimeout(max(0.01, end - time.time()))
        try: data = s.recv(1024)
        except socket.timeout: return None, b""
        if len(data) < 6: continue
        ev, _idx, plen = struct.unpack("<HHH", data[:6])
        payload = data[6:6+plen]
        if ev in (MGMT_EV_CMD_COMPLETE, MGMT_EV_CMD_STATUS) and len(payload) >= 3:
            opcode, status = struct.unpack("<HB", payload[:3])
            if opcode == want:
                return status, payload[3:]
    return None, b""

def conn_info(s, addr6, atype):
    send(s, MGMT_OP_GET_CONN_INFO, addr6 + bytes([atype]))
    status, resp = wait(s, MGMT_OP_GET_CONN_INFO)
    if status != 0 or len(resp) < 10: return None
    rssi = struct.unpack("b", resp[7:8])[0]
    tx   = struct.unpack("b", resp[8:9])[0]
    return rssi, tx

def main():
    try:
        s = open_mgmt()
    except PermissionError:
        print("PERM: mgmt socket needs privilege"); return
    target6 = a2b(TARGET)
    print(f"polling {TARGET} for {DUR:.0f}s — HOLD a remote key down; Ctrl-C to stop")
    atypes = [1, 2, 0]  # LE public, LE random, BR/EDR
    t0 = time.time(); got = 0; last_wait = 0.0
    while time.time() - t0 < DUR:
        info = None
        for at in list(atypes):
            info = conn_info(s, target6, at)
            if info:
                atypes = [at]; break
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        if info:
            got += 1
            bars = "#" * max(0, min(20, (info[0] + 100) // 3))  # ~ -100..-40 -> 0..20
            print(f"{ts}  RSSI {info[0]:>4} dBm   TX {info[1]:>3}   {bars}", flush=True)
        else:
            if time.time() - last_wait > 1.0:
                print(f"{ts}  ...waiting (hold a key so it stays connected)...", flush=True)
                last_wait = time.time()
        time.sleep(0.12)
    print(f"done -- {got} readings captured")

main()
