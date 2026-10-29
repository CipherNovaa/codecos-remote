#!/usr/bin/env python3
"""Direct PC implementation of the OFA app's e-paper uploader.

``plan`` is offline. ``execute`` sends the app-equivalent 57 A1/A2 packets
through the normal OFA control characteristic and advances only after a remote
notification. It has no retries, resume, or automatic recovery; use a known
good screen image for the first live round trip.
"""
import ofa_identity
import argparse
import asyncio
import hashlib
import json
import os
import zlib
from datetime import datetime, timezone
from pathlib import Path

ADDRESS = ofa_identity.CONFIG_ADDR
WR = '00001121-d102-11e1-9b23-00025b00a5a5'
NT = '00001122-d102-11e1-9b23-00025b00a5a5'
MODEL = '00002a24-0000-1000-8000-00805f9b34fb'
FIRMWARE = '00002a26-0000-1000-8000-00805f9b34fb'
BLOCK = 128


def build(image: bytes) -> tuple[bytes, list[bytes], dict]:
    """Exact OFADeviceHandler.dbUpgrade e-ink packet construction."""
    padded = image + bytes((-len(image)) % 512)
    count = len(padded) // BLOCK
    if not 1 <= count <= 0xffff:
        raise ValueError('Image packet count is outside app protocol limits')
    start = b'W\xa1' + count.to_bytes(2, 'little') + zlib.crc32(padded).to_bytes(4, 'little')
    packets = [b'W\xa2' + sequence.to_bytes(2, 'little') + padded[offset:offset + BLOCK] +
               zlib.crc32(padded[offset:offset + BLOCK]).to_bytes(4, 'little')
               for sequence, offset in enumerate(range(0, len(padded), BLOCK))]
    if any(len(packet) != 136 for packet in packets):
        raise AssertionError('Invalid A2 framing')
    return start, packets, {'image_sha256': hashlib.sha256(image).hexdigest(), 'image_bytes': len(image),
                             'padded_bytes': len(padded), 'packet_count': count,
                             'start': start.hex(' '), 'first_packet': packets[0][:8].hex(' '),
                             'last_packet': packets[-1][:8].hex(' ')}


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


async def upload(args, image, result, persist):
    from bleak import BleakClient
    start, packets, _ = build(image)
    async with BleakClient(args.address, timeout=25) as client:
        model = bytes(await client.read_gatt_char(MODEL)).rstrip(b'\0').decode('ascii', 'replace')
        firmware = bytes(await client.read_gatt_char(FIRMWARE)).rstrip(b'\0').decode('ascii', 'replace')
        if (model, firmware) != ('URC7985 R00', '0.5.7'):
            raise ValueError(f'Unexpected remote identity: {(model, firmware)}')
        try:
            await client._backend._acquire_mtu()
        except Exception:
            pass
        characteristic = client.services.get_characteristic(WR)
        limit = max(characteristic.max_write_without_response_size, (client.mtu_size or 23) - 3)
        if limit < len(packets[0]):
            raise ValueError(f'Need ATT payload >=136 bytes; negotiated {limit}')
        queue = asyncio.Queue()
        await client.start_notify(NT, lambda _, data: queue.put_nowait(bytes(data)))
        result['identity'] = {'model': model, 'firmware': firmware, 'att_payload_limit': limit}
        persist()

        async def send_and_wait(payload, kind, sequence=None):
            event = {'time': datetime.now(timezone.utc).isoformat(), 'kind': kind,
                     'sequence': sequence, 'write': payload.hex(' ')}
            result['events'].append(event); persist()
            await client.write_gatt_char(WR, payload, response=False)
            try:
                reply = await asyncio.wait_for(queue.get(), timeout=args.timeout)
            except TimeoutError as error:
                raise TimeoutError(f'No acknowledgement after {kind} {sequence}') from error
            event['notification'] = reply.hex(' '); persist()
            # The known-good e-paper controller acknowledges start as a1 02
            # and every numbered data packet as a2 02.  No retry is safe.
            expected = b'\xa1\x02' if kind == 'start' else b'\xa2\x02'
            if reply != expected:
                raise ValueError(
                    f'Unexpected acknowledgement after {kind} {sequence}: '
                    f'{reply.hex(" ")} (expected {expected.hex(" ")})'
                )

        await send_and_wait(start, 'start')
        for sequence, packet in enumerate(packets):
            await send_and_wait(packet, 'data', sequence)
            if sequence % 100 == 99 or sequence + 1 == len(packets):
                print(f'{sequence + 1}/{len(packets)} acknowledged', flush=True)
        result['status'] = 'all app-protocol packets acknowledged; verify screen boot visually'
        persist()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    for name in ('plan', 'execute'):
        q = sub.add_parser(name); q.add_argument('image', type=Path)
    execute = sub.choices['execute']
    execute.add_argument('--address', default=ADDRESS); execute.add_argument('--timeout', type=float, default=12)
    execute.add_argument('--log', type=Path, required=True)
    execute.add_argument('--confirm', default='')
    args = p.parse_args()
    image = args.image.read_bytes()
    _, _, plan = build(image)
    if args.action == 'plan':
        print(json.dumps({'status': 'OFFLINE PLAN — NO BLUETOOTH', **plan}, indent=2)); return
    if args.confirm != 'FLASH E-PAPER DIRECT':
        p.error("execution requires --confirm 'FLASH E-PAPER DIRECT'")
    if args.log.exists():
        raise FileExistsError(args.log)
    result = {'schema': 'ofa-screen-direct-1', 'status': 'starting', 'address': args.address,
              'plan': plan, 'events': []}
    save(args.log, result)
    try:
        asyncio.run(upload(args, image, result, lambda: save(args.log, result)))
    except BaseException as error:
        result['status'] = 'stopped; inspect journal before any further screen operation'
        result['error'] = repr(error); save(args.log, result); raise


if __name__ == '__main__':
    main()
