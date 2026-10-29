#!/usr/bin/env python3
"""Read, enable, disable and restore OFA URC7985 touchscreen pages per device.

Copies layouts already present on the remote; never changes a device record,
pairing or firmware. Phone Bluetooth must be off; hold ACTIVITIES + DEVICES.
"""
import ofa_identity
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

ADDRESS = ofa_identity.CONFIG_ADDR
WR = '00001121-d102-11e1-9b23-00025b00a5a5'
NT = '00001122-d102-11e1-9b23-00025b00a5a5'
DIGITS = set(range(0x0D, 0x17))


def check_slot(slot):
    if type(slot) is not int or not 1 <= slot <= 12:
        raise ValueError('Device slot must be 1..12')


def validate_pages(pages):
    if not isinstance(pages, list) or len(pages) > 10:
        raise ValueError('Expected at most ten pages')
    previous = 0
    for page in pages:
        if set(page) != {'id', 'layout', 'keys'}:
            raise ValueError('Page fields must be id, layout, keys')
        if type(page['id']) is not int or not previous < page['id'] <= 10:
            raise ValueError('Page IDs must be distinct, ascending and 1..10')
        previous = page['id']
        if type(page['layout']) is not int or not 0 <= page['layout'] <= 255:
            raise ValueError('Invalid layout byte')
        keys = page['keys']
        if not isinstance(keys, list) or not 1 <= len(keys) <= 16:
            raise ValueError('Each page must contain 1..16 keys')
        if any(type(k) is not int or not 1 <= k <= 255 for k in keys):
            raise ValueError('Invalid logical key ID')
        if 2 + len(keys) + 16 * sum(k >= 0xF0 for k in keys) > 124:
            raise ValueError('Labels would exceed the firmware screen-message buffer')


def decode_pages(data):
    if len(data) < 2 or data[0] != 7 or data[1] > 10:
        raise ValueError(f'Invalid W 07 reply: {data.hex(" ")}')
    pages, offset = [], 2
    for _ in range(data[1]):
        if offset + 3 > len(data):
            raise ValueError('Truncated page header')
        page_id, layout, count = data[offset:offset + 3]
        offset += 3
        if offset + count > len(data):
            raise ValueError('Truncated page keys')
        pages.append({'id': page_id, 'layout': layout,
                      'keys': list(data[offset:offset + count])})
        offset += count
    if offset != len(data):
        raise ValueError('Unexpected trailing page data')
    validate_pages(pages)
    return pages


def encode_pages(slot, pages):
    check_slot(slot)
    validate_pages(pages)
    result = bytearray([0x57, 0x17, slot, len(pages)])
    for page in pages:
        result.extend([page['id'], page['layout'], len(page['keys']), *page['keys']])
    if not pages:
        # The zero-page firmware branch still examines byte 4. Supply a
        # nonmatching ID explicitly, so it never reads past the command.
        result.append(0)
    return bytes(result)


def decode_inventory(data):
    if len(data) < 265 or data[0] != 2:
        raise ValueError('Incomplete W 02 inventory')
    records = []
    for index in range(12):
        raw = data[1 + 22 * index:23 + 22 * index]
        if not any(raw):
            continue  # Holes are allowed; do not stop at the first empty slot.
        slot = raw[0] & 15
        check_slot(slot)
        records.append({'position': index, 'slot': slot, 'type': raw[0] >> 4,
                        'name': raw[6:].split(b'\0')[0].decode('utf-8', errors='replace'),
                        'raw': raw.hex()})
    return records


def slot_identity(snapshot, slot):
    matches = {r['raw'] for r in snapshot['inventory'] if r['slot'] == slot}
    if len(matches) != 1:
        raise ValueError(f'Slot {slot} is missing or has conflicting device records')
    return matches.pop()


def select_pages(pages, selection):
    if selection == 'all':
        return pages[:]
    if selection == 'digits':
        return [p for p in pages if DIGITS.issubset(p['keys'])]
    try:
        ids = {int(part) for part in selection.split(',')}
    except ValueError as error:
        raise ValueError('--pages must be all, digits, or comma-separated page IDs') from error
    selected = [p for p in pages if p['id'] in ids]
    if {p['id'] for p in selected} != ids:
        raise ValueError('A requested page ID is absent')
    return selected


def merge_pages(existing, selected):
    result = json.loads(json.dumps(existing))
    for page in selected:
        if any(k >= 0xF0 for k in page['keys']):
            raise ValueError('Custom-label keys cannot be copied between device slots')
        if any(p['layout'] == page['layout'] and p['keys'] == page['keys'] for p in result):
            continue
        free = sorted(set(range(1, 11)) - {p['id'] for p in result})
        if not free:
            raise ValueError('Target already has ten pages')
        result.append({'id': free[0], 'layout': page['layout'], 'keys': page['keys'][:]})
    result.sort(key=lambda p: p['id'])
    validate_pages(result)
    return result


class Config:
    OPCODES = (2, 7, 0x17, 0x19)

    def __init__(self, client):
        self.client = client
        self.queue = asyncio.Queue()
        self.transactions = []

    async def start(self):
        await self.client.start_notify(NT, lambda _, data: self.queue.put_nowait(bytes(data)))
        # bleak/BlueZ reports the default MTU (23) until it acquires the real one,
        # which makes max_write_without_response_size a stale 20 and wrongly rejects
        # a full page command. Acquire the negotiated MTU (247 here) up front.
        try:
            await self.client._backend._acquire_mtu()
        except Exception:
            pass

    async def command(self, payload):
        # No arbitrary config command escape hatch.
        if len(payload) < 2 or payload[0] != 0x57 or payload[1] not in self.OPCODES:
            raise ValueError('Command is outside the touchscreen tool allowlist')
        if not self.queue.empty():
            raise ValueError('Unsolicited config data; refusing an ambiguous transaction')
        characteristic = self.client.services.get_characteristic(WR)
        # Use the real negotiated MTU (mtu_size-3) when known; fall back to the
        # characteristic's reported write size. Guards against a genuinely tiny MTU.
        write_limit = max(characteristic.max_write_without_response_size,
                          (self.client.mtu_size or 23) - 3)
        if len(payload) > write_limit:
            raise ValueError('Negotiated BLE packet size is too small for this page command')
        entry = {'command': payload.hex(' '), 'notifications': []}
        self.transactions.append(entry)
        await self.client.write_gatt_char(WR, payload, response=False)
        chunks = []
        deadline = asyncio.get_running_loop().time() + 8
        while True:
            timeout = min(0.5 if chunks else 5, deadline - asyncio.get_running_loop().time())
            if timeout <= 0:
                raise TimeoutError('Config response did not finish')
            try:
                data = await asyncio.wait_for(self.queue.get(), timeout)
            except asyncio.TimeoutError:
                if chunks:
                    break
                raise TimeoutError('No config reply; reconnect in ACTIVITIES + DEVICES mode')
            chunks.append(data)
            entry['notifications'].append(data.hex(' '))
            if sum(map(len, chunks)) > 2048:
                raise ValueError('Unexpectedly large config response')
        reply = b''.join(chunks)
        if not reply or reply[0] != payload[1]:
            raise ValueError('Reply opcode does not match the request')
        return reply

    async def snapshot(self):
        values = {}
        for field, short in [('model', '2a24'), ('firmware', '2a26')]:
            data = await self.client.read_gatt_char(f'0000{short}-0000-1000-8000-00805f9b34fb')
            values[field] = bytes(data).rstrip(b'\0').decode('ascii')
        if values != {'model': 'URC7985 R00', 'firmware': '0.5.7'}:
            raise ValueError(f'Unsupported remote identity: {values}')
        inventory = decode_inventory(await self.command(b'W\x02'))
        pages = {}
        for slot in sorted({r['slot'] for r in inventory}):
            slot_identity({'inventory': inventory}, slot)
            pages[str(slot)] = decode_pages(await self.command(bytes([0x57, 7, slot])))
        return {'identity': values, 'inventory': inventory, 'pages': pages}


def summarize(snapshot):
    for slot, pages in snapshot['pages'].items():
        record = next(r for r in snapshot['inventory'] if r['slot'] == int(slot))
        descriptions = ', '.join(f"page {p['id']} / layout {p['layout']:02x} / "
                                 f"{len(p['keys'])} keys" +
                                 (' / digits' if DIGITS.issubset(p['keys']) else '') for p in pages)
        print(f"slot {slot}: {record['name']} (type {record['type']}): {descriptions or 'no pages'}",
              flush=True)


async def run(args, journal, save):
    from bleak import BleakClient
    source_backup = None
    if args.action == 'restore':
        source_backup = json.loads(args.backup.read_text())
        if source_backup.get('schema') != 1 or source_backup.get('address') != args.address:
            raise ValueError('Backup belongs to a different remote or format')
        args.slot = source_backup['slot']
        check_slot(args.slot)
    async with BleakClient(args.address, timeout=25) as client:
        config = Config(client)
        journal['transactions'] = config.transactions
        await config.start()
        before = await config.snapshot()
        journal['before'] = before
        save()
        summarize(before)
        if args.action == 'inspect':
            journal['status'] = 'read_complete'
            return
        check_slot(args.slot)
        identity = slot_identity(before, args.slot)
        existing = before['pages'][str(args.slot)]
        if args.action == 'enable':
            check_slot(args.from_slot)
            slot_identity(before, args.from_slot)
            selected = select_pages(before['pages'][str(args.from_slot)], args.pages)
            if not selected:
                raise ValueError('No matching source page is available')
            desired = merge_pages(existing, selected)
        elif args.action == 'disable':
            selected = select_pages(existing, args.pages)
            desired = [p for p in existing if p not in selected]
        else:
            previous = source_backup['before']
            if slot_identity(previous, args.slot) != identity:
                raise ValueError('Target device identity changed since backup')
            desired = previous['pages'][str(args.slot)]
            if existing not in (desired, source_backup['desired']):
                raise ValueError('Pages changed independently since backup; refusing to overwrite')
        payload = encode_pages(args.slot, desired)
        journal.update(slot=args.slot, desired=desired, write_command=payload.hex(' '))
        if existing == desired:
            journal['status'] = 'already_matches'
            print('Pages already match; no write needed.', flush=True)
            return
        if args.dry_run:
            journal['status'] = 'dry_run'
            print(f"Would write slot {args.slot}: {payload.hex(' ')}", flush=True)
            return
        # Persist the original pages before the only mutating command.
        journal['status'] = 'write_pending'
        save()
        reply = await config.command(payload)
        if reply != bytes([0x17, 1]):
            raise ValueError(f'Page write was not acknowledged: {reply.hex(" ")}')
        journal['status'] = 'acknowledged'
        save()
        after = await config.snapshot()
        journal['after'] = after
        expected = dict(before['pages'], **{str(args.slot): desired})
        if after['inventory'] != before['inventory'] or after['pages'] != expected:
            raise ValueError('Read-back differs from the intended single-slot page change')
        journal['status'] = 'verified'
        print(f'Slot {args.slot}: pages saved and read back; other profiles unchanged.', flush=True)
        print('Select another device, then this device, to reload its touchscreen pages.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', default=ADDRESS)
    commands = parser.add_subparsers(dest='action', required=True)
    inspect = commands.add_parser('inspect', help='Save device records and all configured page sets')
    inspect.add_argument('--output', type=Path, required=True)
    for action in ('enable', 'disable'):
        command = commands.add_parser(action)
        command.add_argument('--slot', type=int, required=True)
        if action == 'enable':
            command.add_argument('--from-slot', type=int, required=True)
        command.add_argument('--pages', default='all', help='all, digits, or page IDs such as 1,2')
        command.add_argument('--backup', type=Path, required=True, dest='output')
        command.add_argument('--dry-run', action='store_true')
    restore = commands.add_parser('restore', help='Restore the original pages from a prior operation')
    restore.add_argument('--backup', type=Path, required=True)
    restore.add_argument('--save-backup', type=Path, required=True, dest='output')
    restore.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if hasattr(args, 'slot'):
        check_slot(args.slot)
    journal = {'schema': 1, 'address': args.address, 'action': args.action,
               'time': datetime.now(timezone.utc).isoformat(), 'status': 'starting'}
    # Refuse to overwrite evidence from an earlier operation.
    with args.output.open('x') as output:
        json.dump(journal, output, indent=2)

    def save():
        # Write a complete replacement before swapping it into place, so a
        # failed later save cannot truncate the original page backup.
        temporary = args.output.with_name(args.output.name + '.tmp')
        with temporary.open('x') as output:
            json.dump(journal, output, indent=2)
            output.write('\n')
            output.flush()
            import os
            os.fsync(output.fileno())
        temporary.replace(args.output)

    try:
        asyncio.run(run(args, journal, save))
    except Exception as error:
        journal['error'] = str(error)
        save()
        parser.exit(1, f'{error}\nCapture/backup: {args.output}\n')
    finally:
        save()
    print(f"Saved {args.output}")


if __name__ == '__main__':
    main()
