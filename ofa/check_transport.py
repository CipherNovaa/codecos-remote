#!/usr/bin/env python3
"""Compare our encryption to the APK's actual AArch64 JNI code in Unicorn."""
import argparse
import struct
from pathlib import Path

from dfu import encrypted_block, load_jni, parse_image_info, transfer_plan


def native_encrypt(library, block):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                    UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                                    UC_ARM64_REG_X4, UC_ARM64_REG_X30,
                                    UC_ARM64_REG_SP, UC_ARM64_REG_PC)

    cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    cpu.mem_map(0, 0x10000)
    cpu.mem_write(0, library[:0x40D0])
    cpu.mem_write(0x80D0, library[0x40D0:0x42E0])
    cpu.mem_write(0x8298, struct.pack("<Q", 0xC2E0))  # relocated ctx GOT entry
    cpu.mem_map(0x100000, 0x1000)
    cpu.mem_map(0x200000, 0x10000)
    cpu.mem_write(0x200000, struct.pack("<Q", 0x201000))  # JNI environment
    for offset, callback in ((0x558, 0x100000), (0x640, 0x100010), (0x680, 0x100020)):
        cpu.mem_write(0x201000 + offset, struct.pack("<Q", callback))
    result = []

    def hook(uc, address, size, _):
        if address == 0x40A0:  # aes_set_key PLT
            uc.reg_write(UC_ARM64_REG_PC, 0x2D84)
            return
        if address not in (0x100000, 0x100010, 0x100020):
            return
        if address == 0x100000:
            uc.reg_write(UC_ARM64_REG_X0, len(block))
        elif address == 0x100010:
            uc.mem_write(uc.reg_read(UC_ARM64_REG_X4), block)
        else:
            result.append(bytes(uc.mem_read(uc.reg_read(UC_ARM64_REG_X4), 16)))
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    cpu.hook_add(UC_HOOK_CODE, hook)
    cpu.reg_write(UC_ARM64_REG_SP, 0x20F000)
    cpu.reg_write(UC_ARM64_REG_X30, 0x100FF0)
    cpu.reg_write(UC_ARM64_REG_X0, 0x200000)
    cpu.reg_write(UC_ARM64_REG_X2, 3)
    cpu.reg_write(UC_ARM64_REG_X3, 0)  # actual app default aesInit(3, null)
    cpu.emu_start(0x2CA8, 0x100FF0, count=20000)
    assert cpu.reg_read(UC_ARM64_REG_X0) == 1, "Native AES initialization failed"
    cpu.reg_write(UC_ARM64_REG_X30, 0x100FF0)
    cpu.reg_write(UC_ARM64_REG_X0, 0x200000)
    cpu.reg_write(UC_ARM64_REG_X2, 0x202000)
    cpu.reg_write(UC_ARM64_REG_X3, 0x203000)
    cpu.emu_start(0x33B8, 0x100FF0, count=20000)
    assert len(result) == 1, "Native encryption did not finish"
    return result[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    library = load_jni(args.apk)
    image = args.image.read_bytes()
    blocks = [bytes(16), bytes(range(16)), bytes([255]) * 16,
              image[512:524] + bytes(4), image[524:540], image[-16:]]
    for block in blocks:
        assert native_encrypt(library, block) == encrypted_block(library, block)
    print(f"PASS: {len(blocks)} blocks match the actual emulated APK AES implementation")
    plan = transfer_plan(image, library)
    assert plan["data_packets"] * 16 == len(image) - 524
    assert parse_image_info(bytes.fromhex("10 06 01 00 50 00 38 00 00 00 00"))["version"] == "0x38005000"
    for response in (b"", bytes.fromhex("10 06 05"), bytes.fromhex("10 01 01")):
        try:
            parse_image_info(response)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid DFU response accepted")
    print("PASS: encrypted packet count and response validation; no Bluetooth writes performed")


if __name__ == "__main__":
    main()
