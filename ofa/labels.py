#!/usr/bin/env python3
"""Set independent touchscreen labels on the custom-touch firmware.

Defaults to a live read + plan. --apply writes with a fresh backup and readback.
Needs CT01 firmware, phone Bluetooth off, and ACTIVITIES + DEVICES mode.
Example: labels.py set --slot 1 --key red --text SUBS --backup change.json --apply
Labels use additional text pages; the original numeric keypad is preserved.
Keys: red green yellow blue return info (aliases fa..ff) and, on the spare-keys
firmware, spare1..spare10 (aliases f0..f9). Six keys per added page.
"""
import argparse
import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from custom_firmware import ALIASES
from touch_pages import ADDRESS, Config, check_slot, encode_pages, slot_identity, validate_pages

KEYS_PER_LABEL_PAGE = 6  # Two columns x three rows; 104-byte serialized page.


def label_bytes(text):
    data = text.encode('ascii')
    if not 1 <= len(data) <= 15 or any(not 32 <= c <= 126 for c in data):
        raise ValueError('Label must contain 1..15 printable ASCII characters (plus a NUL terminator)')
    return data.ljust(16, b'\0')


def add_label_page(pages, alias):
    result = copy.deepcopy(pages)
    if any(alias in p['keys'] for p in result):
        return result
    for page in result:
        if page['layout'] == 0xFF and all(k >= 0xF0 for k in page['keys']) and len(page['keys']) < KEYS_PER_LABEL_PAGE:
            page['keys'].append(alias)
            validate_pages(result)
            return result
    free = sorted(set(range(1, 11)) - {p['id'] for p in result})
    if not free:
        raise ValueError('No free touchscreen page; remove an unused page first')
    result.append({'id': free[0], 'layout': 0xFF, 'keys': [alias]})
    result.sort(key=lambda p: p['id'])
    validate_pages(result)
    return result


def compact_label_pages(pages):
    """Pack consecutive custom-text pages; retain all keys and other layouts."""
    validate_pages(pages)
    result, group = [], []

    def flush():
        keys = [key for page in group for key in page['keys']]
        chunks = [keys[i:i + KEYS_PER_LABEL_PAGE] for i in range(0, len(keys), KEYS_PER_LABEL_PAGE)]
        if len(chunks) > len(group):
            result.extend(copy.deepcopy(group))  # Never add pages while compacting.
        else:
            result.extend({'id': page['id'], 'layout': 0xFF, 'keys': chunk}
                          for page, chunk in zip(group, chunks))
        group.clear()

    for page in pages:
        if page['layout'] == 0xFF and all(k >= 0xF0 for k in page['keys']):
            group.append(page)
        else:
            flush()
            result.append(copy.deepcopy(page))
    flush()
    validate_pages(result)
    return result


class LabelConfig(Config):
    OPCODES = Config.OPCODES + (0x0B,)

    async def capabilities(self):
        if await self.command(b'W\x0b\x00') != b'\x0b\x01CT01':
            raise ValueError('Remote has not confirmed the CT01 custom-label firmware')

    async def read_label(self, slot, alias):
        reply = await self.command(bytes([0x57, 0x0B, 3, slot, alias]))
        if len(reply) != 19 or reply[:2] != b'\x0b\x01' or reply[2] not in (0, 1):
            raise ValueError(f'Label read failed: {reply.hex()}')
        return reply[3:].hex() if reply[2] else None

    async def write_label(self, slot, alias, value):
        data = bytes([0x57, 0x0B, 2 if value is None else 1, slot, alias])
        if value is not None:
            raw = bytes.fromhex(value)
            if len(raw) != 16 or label_bytes(raw.rstrip(b'\0').decode('ascii')) != raw:
                raise ValueError('Invalid saved label')
            data += raw
        if await self.command(data) != b'\x0b\x01':
            raise ValueError('Label write rejected; inspect the saved transaction log')


async def compact(args, journal, save):
    from bleak import BleakClient
    async with BleakClient(args.address, timeout=25) as client:
        config = LabelConfig(client)
        journal['transactions'] = config.transactions
        await config.start()
        await config.capabilities()
        before = await config.snapshot()
        slot_identity(before, args.slot)
        record = next(r for r in before['inventory'] if r['slot'] == args.slot)
        if record['type'] != 4:
            raise ValueError('Compact supports type-4 BLE devices only')
        existing = before['pages'][str(args.slot)]
        desired = compact_label_pages(existing)
        aliases = sorted({k for page in existing for k in page['keys'] if k >= 0xF0})
        labels = {k: await config.read_label(args.slot, k) for k in aliases}
        if any(value is None for value in labels.values()):
            raise ValueError('A visible custom key has no saved label')
        # Schema 1 makes this page-only operation reversible with touch_pages.py restore.
        journal.update(slot=args.slot, before=before, desired=desired,
                       original_labels=labels, status='planned')
        save()
        print(json.dumps(desired, indent=2), flush=True)
        if not args.apply:
            print('Read + plan only. Use --apply and a new --backup path to write.')
            return
        if desired != existing:
            journal['status'] = 'write_pending'
            save()
            if await config.command(encode_pages(args.slot, desired)) != b'\x17\x01':
                raise ValueError('Page write rejected')
        after = await config.snapshot()
        actual_labels = {k: await config.read_label(args.slot, k) for k in aliases}
        expected = copy.deepcopy(before)
        expected['pages'][str(args.slot)] = desired
        journal['after'] = after
        if after != expected or actual_labels != labels:
            raise ValueError('Readback differs from the intended page-only change')
        journal['status'] = 'verified'
        save()
        print('Pages compacted and verified; labels and other devices unchanged. Reselect the device to refresh.')


async def run(args, journal, save):
    from bleak import BleakClient
    if args.action == 'compact':
        return await compact(args, journal, save)
    restoring = json.loads(args.source.read_text()) if args.action == 'restore' else None
    if restoring:
        if restoring.get('schema') != 'ofa-label-1' or restoring.get('address') != args.address:
            raise ValueError('Backup format/remote mismatch')
        if restoring.get('status') != 'verified':
            raise ValueError('Restore requires a verified prior operation; partial writes need inspection')
        args.slot, alias = restoring['slot'], restoring['alias']
    else:
        alias = ALIASES[args.key]
    check_slot(args.slot)
    journal.update(slot=args.slot, alias=alias)
    async with BleakClient(args.address, timeout=25) as client:
        config = LabelConfig(client)
        journal['transactions'] = config.transactions
        await config.start()
        await config.capabilities()
        before = await config.snapshot()
        identity = slot_identity(before, args.slot)
        record = next(r for r in before['inventory'] if r['slot'] == args.slot)
        if record['type'] != 4:
            raise ValueError('Labels are enabled only on type-4 BLE devices')
        original = await config.read_label(args.slot, alias)
        existing = before['pages'][str(args.slot)]
        if restoring:
            if identity != slot_identity(restoring['before'], args.slot):
                raise ValueError('Device record changed since the saved operation')
            if existing != restoring['desired_pages'] or original != restoring['desired_label']:
                raise ValueError('Pages or label have changed since that operation; refusing stale restore')
            pages, value = restoring['before']['pages'][str(args.slot)], restoring['original_label']
        elif getattr(args, 'clear', False):
            pages = existing                 # keep the page layout; just drop the saved label
            value = None
        else:
            pages = add_label_page(existing, alias)
            value = label_bytes(args.text).hex()
        journal.update(before=before, original_label=original, desired_label=value,
                       desired_pages=pages, status='planned')
        save()
        print(f"{record['name']} / {args.key if not restoring else hex(alias)}: "
              f"{bytes.fromhex(value).rstrip(bytes(1)).decode('ascii') if value else '(removed)'}", flush=True)
        print(json.dumps(pages, indent=2), flush=True)
        if not args.apply:
            print('Read + plan only. Use --apply and a new --backup path to write.')
            return
        # Set before displaying; when removing, hide before deleting the label.
        journal['status'] = 'writing'
        save()
        if value is not None:
            await config.write_label(args.slot, alias, value)
        if pages != existing:
            if await config.command(encode_pages(args.slot, pages)) != b'\x17\x01':
                raise ValueError('Page write rejected')
        if value is None:
            await config.write_label(args.slot, alias, None)
        actual = await config.read_label(args.slot, alias)
        after = await config.snapshot()
        expected = copy.deepcopy(before)
        expected['pages'][str(args.slot)] = pages
        journal['after'] = after
        if actual != value or after != expected:
            raise ValueError('Readback differs from the intended label/page change')
        journal['status'] = 'verified'
        save()
        print('Saved and read back. Select another device, then this device, to refresh its pages.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--address', default=ADDRESS)
    subs = p.add_subparsers(dest='action', required=True)
    setting = subs.add_parser('set')
    setting.add_argument('--slot', type=int, required=True)
    setting.add_argument('--key', choices=ALIASES, required=True)
    setting.add_argument('--text')
    setting.add_argument('--clear', action='store_true',
                         help='remove the key\'s custom label (leaves the page in place)')
    restore = subs.add_parser('restore')
    restore.add_argument('--source', type=Path, required=True)
    packing = subs.add_parser('compact', help='Combine custom-text pages, up to six buttons each')
    packing.add_argument('--slot', type=int, required=True)
    for sub in (setting, restore, packing):
        sub.add_argument('--backup', type=Path, required=True)
        sub.add_argument('--apply', action='store_true')
    args = p.parse_args()
    if args.action in ('set', 'compact'):
        check_slot(args.slot)
    if args.action == 'set':
        if args.clear and args.text is not None:
            p.error('--clear and --text are mutually exclusive')
        if not args.clear and args.text is None:
            p.error('one of --text or --clear is required')
        if args.text is not None:
            label_bytes(args.text)
    journal = {'schema': 1 if args.action == 'compact' else 'ofa-label-1',
               'address': args.address, 'action': args.action,
               'time': datetime.now(timezone.utc).isoformat(), 'status': 'starting'}
    with args.backup.open('x') as stream:
        json.dump(journal, stream)

    def save():
        temporary = args.backup.with_name(args.backup.name + '.tmp')
        with temporary.open('x') as stream:
            json.dump(journal, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(args.backup)
    try:
        asyncio.run(run(args, journal, save))
    except Exception as error:
        journal['error'] = str(error)
        p.exit(1, f'{error}\nBackup: {args.backup}\n')
    finally:
        save()


if __name__ == '__main__':
    main()
