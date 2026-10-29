#!/usr/bin/env python3
"""Reproduce the stock app's E-paper OTA packet construction offline.

This program has no Bluetooth imports or I/O.  It validates only the packet
stream produced by OFA 1.0.7's already-installed uploader, so it is safe to
run before an explicitly approved on-device stock recovery round trip.
"""
import argparse
import hashlib
import json
import zlib
from pathlib import Path


APP_BLOCK = 512
BLE_PAYLOAD = 128
HEADER = bytes((0x57, 0xA1))
DATA = bytes((0x57, 0xA2))


def crc32le(data):
    """Dart _calculateCrc32FromBytes: reflected CRC-32, serialized LE."""
    return zlib.crc32(data).to_bytes(4, 'little')


def build(image):
    # OFADeviceHandler.screenUpgrade copies each source 512-byte block and
    # zero-fills its last partial block before flattening it for dbUpgrade.
    padded_length = ((len(image) + APP_BLOCK - 1) // APP_BLOCK) * APP_BLOCK
    padded = image + bytes(padded_length - len(image))
    packet_count = padded_length // BLE_PAYLOAD
    if packet_count > 0xFFFF:
        raise ValueError('packet count will not fit the app protocol')

    start = HEADER + packet_count.to_bytes(2, 'little') + crc32le(padded)
    packets = [DATA + sequence.to_bytes(2, 'little') +
               padded[offset:offset + BLE_PAYLOAD] +
               crc32le(padded[offset:offset + BLE_PAYLOAD])
               for sequence, offset in enumerate(range(0, padded_length, BLE_PAYLOAD))]
    if len(packets) != packet_count or any(len(packet) != 136 for packet in packets):
        raise AssertionError('invalid packet partition')
    if b''.join(packet[4:132] for packet in packets) != padded:
        raise AssertionError('packets do not reconstruct padded image')

    return {
        'status': 'DRY RUN ONLY; NO BLUETOOTH WAS OPENED',
        'image_sha256': hashlib.sha256(image).hexdigest(),
        'image_bytes': len(image),
        'app_final_block_padding_bytes': padded_length - len(image),
        'app_padded_bytes': padded_length,
        'ble_payload_bytes': BLE_PAYLOAD,
        'notification_paced_data_packet_count': packet_count,
        'start_packet': start.hex(' '),
        'first_data_packet_prefix': packets[0][:4].hex(' '),
        'first_data_packet_crc32_le': packets[0][-4:].hex(' '),
        'last_sequence': packet_count - 1,
        'last_data_packet_prefix': packets[-1][:4].hex(' '),
        'last_data_packet_crc32_le': packets[-1][-4:].hex(' '),
        'transport': ('Stock OFA 1.0.7 uploader: write start packet, wait for '
                      'notifications, then write one packet per acknowledgement.'),
        'limitation': ('This reproduces packet construction only. It does not '
                       'open Bluetooth, write a GATT characteristic, or prove '
                       'screen bootloader recovery.'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.image.read_bytes()), indent=2))


if __name__ == '__main__':
    main()
