#!/usr/bin/env python3
"""Exercise transfer failure paths against a fake receiver; no Bluetooth access."""
import argparse
import asyncio
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from dfu import CP, DATA, load_jni
from firmware import MP_SIZE, verify_image
from flash import transfer


class Receiver:
    def __init__(self, library, failure=None):
        self.library, self.failure = library, failure
        self.commands, self.payload = [], bytearray()
        self.validated = self.activated = False

    async def start_notify(self, characteristic, callback):
        assert characteristic == CP
        self.callback = callback

    def decrypt(self, data):
        decryptor = Cipher(algorithms.AES(self.library[0x690:0x6B0]), modes.ECB()).decryptor()
        return decryptor.update(data) + decryptor.finalize()

    async def write_gatt_char(self, characteristic, data, *, response):
        if characteristic == DATA:
            assert not response and len(data) == 16
            assert self.commands[:3] == [6, 1, 2]
            if self.failure == "disconnect" and len(self.payload) >= 4096:
                raise ConnectionError("Injected transfer disconnect")
            if self.failure == "async_error" and len(self.payload) >= 4096:
                self.callback(None, bytes.fromhex("10 02 07"))
            self.payload.extend(self.decrypt(data))
            return
        assert characteristic == CP and response
        self.commands.append(data[0])
        answer = None
        if data[0] == 6:
            offset = 4096 if self.failure == "partial" else 0
            answer = bytes.fromhex("10 06 01") + struct.pack("<II", 0x38005000, offset)
        elif data[0] == 1:
            assert len(data) == 17
            header = self.decrypt(data[1:])
            assert header[12:] == bytes(4)
            self.payload.extend(header[:12])
            answer = bytes.fromhex("10 01 02" if self.failure == "start" else "10 01 01")
        elif data[0] == 2:
            assert data == bytes.fromhex("02 93 27 0c 00 00 00")
        elif data[0] == 3:
            assert data == bytes.fromhex("03 93 27")
            verify_image(self.mp + self.payload)
            self.validated = self.failure != "validation"
            answer = bytes.fromhex("10 03 01" if self.validated else "10 03 05")
        elif data[0] == 4:
            assert self.validated, "Activation before successful remote validation"
            self.activated = True
        else:
            raise AssertionError(f"Unexpected opcode: {data[0]}")
        if answer is not None:
            self.callback(None, answer)


async def check(image, library):
    for failure in (None, "partial", "start", "disconnect", "async_error", "validation"):
        receiver = Receiver(library, failure)
        receiver.mp = image[:MP_SIZE]
        try:
            await transfer(receiver, image, library, lambda *a, **k: None, pace=0)
        except (ValueError, ConnectionError):
            assert failure is not None
        else:
            assert failure is None
        if failure is None:
            assert receiver.activated
            assert bytes(receiver.payload) == image[MP_SIZE:]
            assert receiver.commands == [6, 1, 2, 3, 4]
        else:
            assert not receiver.activated and 4 not in receiver.commands
        if failure == "partial":
            assert receiver.commands == [6] and not receiver.payload
    print("PASS: receiver reconstructs exact image from encrypted packets")
    print("PASS: partial update, start rejection, disconnect, async error, and failed validation prevent activation")
    print("No hardware transfer performed; the fake receiver does not prove firmware acceptance or boot.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    asyncio.run(check(args.image.read_bytes(), load_jni(args.apk)))


if __name__ == "__main__":
    main()
