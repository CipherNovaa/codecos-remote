#!/usr/bin/env python3
"""Diagnose duplicate OFA menu entries; optionally remove repeated order entries.

Defaults to read-only. --apply writes W 19 (device order), never W 11 (delete).
Saves the original menu order and verifies unique records and pages afterward.
"""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from touch_pages import ADDRESS, Config, slot_identity


def order_from_inventory(inventory):
    order = [0] * 12
    for record in inventory:
        order[record['position']] = record['slot']
    return order


def deduplicate(order):
    seen, result = set(), []
    for slot in order:
        result.append(0 if slot in seen else slot)
        seen.add(slot)
    return result


async def run(args, journal, save):
    from bleak import BleakClient
    async with BleakClient(args.address, timeout=25) as client:
        config = Config(client)
        journal['transactions'] = config.transactions
        await config.start()
        before = await config.snapshot()
        order = order_from_inventory(before['inventory'])
        desired = deduplicate(order)
        journal.update(before=before, original_order=order, desired_order=desired,
                       write_command=bytes([0x57, 0x19, *desired]).hex(' '),
                       restore_command=bytes([0x57, 0x19, *order]).hex(' '))
        print('Current device order:', order, flush=True)
        print('With repeats removed:', desired, flush=True)
        if order == desired:
            journal['status'] = 'no_duplicates'
            return
        if not args.apply:
            journal['status'] = 'read_only'
            print('Read only; use --apply to change the order.', flush=True)
            return
        journal['status'] = 'write_pending'
        save()
        reply = await config.command(bytes([0x57, 0x19, *desired]))
        if reply != b'\x19\x01':
            raise ValueError(f'Reorder was not acknowledged: {reply.hex(" ")}')
        after = await config.snapshot()
        journal['after'] = after
        old_slots = {r['slot'] for r in before['inventory']}
        new_slots = {r['slot'] for r in after['inventory']}
        if (old_slots != new_slots or after['pages'] != before['pages'] or
                order_from_inventory(after['inventory']) != desired or
                any(slot_identity(before, slot) != slot_identity(after, slot) for slot in old_slots)):
            raise ValueError('Read-back did not match the expected order-only change')
        journal['status'] = 'verified'
        print('Duplicate menu entries removed; unique device records and pages unchanged.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', default=ADDRESS)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    journal = {'schema': 1, 'address': args.address,
               'time': datetime.now(timezone.utc).isoformat(), 'status': 'starting'}
    with args.output.open('x') as output:
        json.dump(journal, output)

    def save():
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
    print('Saved', args.output)


if __name__ == '__main__':
    main()
