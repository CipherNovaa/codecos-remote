#!/usr/bin/env python3
"""Build and validate the RT150T OTA command stream; never uses Bluetooth.

This is a deliberately non-executable dry run.  The URC7985 main firmware
wraps these screen commands in its private UART transport, but the saved
evidence does not contain the phone-to-main OTA ingress protocol.  Do not add
Bluetooth writes here until that missing protocol is independently captured.
"""
import argparse
import hashlib
import json
from pathlib import Path

from screen_fonts import SCREEN_SHA256

BLOCK = 512


def command(opcode, payload=b''):
    # The main encoder at 0x81b5b4/0x81b622/0x81b678 supplies this header.
    # Its lower UART framing/checksum is intentionally not guessed here.
    return bytes.fromhex('4254') + bytes([opcode]) + payload


def build(image):
    if hashlib.sha256(image).hexdigest() != SCREEN_SHA256:
        raise ValueError('Expected the pinned v013f RT150T stock image')
    blocks = [image[offset:offset + BLOCK] for offset in range(0, len(image), BLOCK)]
    if not blocks or len(blocks[-1]) > BLOCK:
        raise AssertionError('Invalid block partition')
    # 0x81b5b4 copies its u32 argument in native (little-endian) order.
    start = command(0x46, len(image).to_bytes(4, 'little'))
    data = [command(0x48, sequence.to_bytes(2, 'big') + block)
            for sequence, block in enumerate(blocks)]
    exit_command = command(0x47)
    if b''.join(frame[5:] for frame in data) != image:
        raise AssertionError('Data frames do not reconstruct the image')
    return {
        'status': 'DRY RUN ONLY; NO BLUETOOTH OR UART WAS OPENED',
        'image_sha256': SCREEN_SHA256,
        'image_bytes': len(image),
        'block_bytes': BLOCK,
        'data_command_count': len(data),
        'first_sequence': 0,
        'last_sequence': len(data) - 1,
        'last_block_bytes': len(blocks[-1]),
        'screen_command_header': '4254',
        'start_command': start.hex(' '),
        'first_data_prefix': data[0][:5].hex(' '),
        'last_data_prefix': data[-1][:5].hex(' '),
        'exit_command': exit_command.hex(' '),
        'limitation': ('The outer phone-to-main OTA ingress and bootloader acknowledgments '
                       'are absent from the saved evidence; this stream is not executable.'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.image.read_bytes()), indent=2))


if __name__ == '__main__':
    main()
