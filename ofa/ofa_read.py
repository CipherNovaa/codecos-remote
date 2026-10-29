#!/usr/bin/env python3
"""Send one config-char (0x1121) command and dump the notify reply. READ-ONLY use.
Default reads the AMPLIFIER (slot 2) page/key table via W07 to expose the logical
key ids the author assigned (Power Off/On, TV input, etc.)."""
import ofa_identity
import argparse, asyncio
WR = "00001121-d102-11e1-9b23-00025b00a5a5"
NT = "00001122-d102-11e1-9b23-00025b00a5a5"


async def run(address, cmd):
    from bleak import BleakClient
    replies = []
    async with BleakClient(address, timeout=25) as client:
        await client.start_notify(NT, lambda _, d: replies.append(bytes(d)))
        await client.write_gatt_char(WR, cmd, response=False)
        await asyncio.sleep(3.0)
    blob = b"".join(replies)
    print("CMD  :", cmd.hex(" "))
    print("LEN  :", len(blob))
    print("REPLY:", blob.hex(" "))
    # Heuristic page-record parse (slot, page_count, then 19-byte records:
    # page_index, presentation, key_count, up to 16 logical key ids).
    if len(blob) > 2 and blob[0] == cmd[1]:
        body = blob[1:]
        print("-- heuristic 19-byte records --")
        i = 2
        while i + 3 <= len(body):
            rec = body[i:i + 19]
            if not any(rec):
                break
            ids = rec[3:3 + (rec[2] if rec[2] <= 16 else 16)]
            print(f"  rec@{i}: pageidx={rec[0]:#04x} pres={rec[1]:#04x} "
                  f"keys={rec[2]} ids={' '.join(f'{b:#04x}' for b in ids)}")
            i += 19


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    ap.add_argument("--cmd", default="570702")
    a = ap.parse_args()
    asyncio.run(run(a.address, bytes.fromhex(a.cmd.replace(" ", ""))))


if __name__ == "__main__":
    main()
