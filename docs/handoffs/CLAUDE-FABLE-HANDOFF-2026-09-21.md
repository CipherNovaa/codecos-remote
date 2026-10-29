# URC7985 LAPTOP Bluetooth provisioning — handoff for Claude Fable

## Goal

Make the OFA URC7985’s `LAPTOP` device slot operate as a second Bluetooth HID
host slot alongside the existing working `DESKTOP` slot. Selecting LAPTOP
should select its host; holding `Vol+` + `OK` on LAPTOP should enter HID
pairing (LED blink and HID advertisement) without destroying DESKTOP’s bond.

The task is analysis only: identify the correct stock state transition and
determine whether the controller design can support two HID hosts.

## Current remote state

- Model: `URC7985 R00`
- Main firmware: `0.5.7`
- E-paper firmware: `0.1.63` / screen asset `013f`
- Normal control address: `<CONFIG_ADDR>`
- Existing working DESKTOP HID identity: `<HID_ADDR_SLOT1>`
- Main application currently contains the `laptop-pairing-v5` branch variant.
  It boots, but it did **not** make LAPTOP enter pairing mode.
- mgs-codec e-paper font is installed and works. A transient corrupted e-paper
  redraw was fixed by removing the remote batteries for 30 seconds.

The most recent `W 02` / page read is
`ofa/artifacts/laptop-current-inspect-20260922.json`. It shows:

```
slot 1: DESKTOP, type 4, code 270b, profile byte 05
slot 2: AMPLIFIER, type 0
slot 3: TELEVISION, type 1
slot 4: LAPTOP, type 4, code 270b, profile byte 05
```

There are two byte-identical visible LAPTOP records in this response. Do not
use `W 11` to remove one: its deletion behavior is not established.

LAPTOP now has the normal numeric touchscreen page copied from DESKTOP and it
was read back successfully:
`ofa/artifacts/laptop-font-page-enable-20260921.json`.

Its custom-label second page is still absent.

## Live behavior

### DESKTOP

- Selecting DESKTOP restores the working HID link to the PC.
- Holding `Vol+` + `OK` blinks the LED.
- Passive PC BlueZ trace saw `<HID_ADDR_SLOT1>` advertise, the prior link
  time out, then the HID device reconnect.
- Trace: `ofa/artifacts/laptop-bt-trace-desktop.txt`.

### LAPTOP

- Selecting LAPTOP deactivates the DESKTOP HID link.
- Holding the same `Vol+` + `OK` gesture does **not** blink the LED.
- Passive trace sees the existing HID identity disconnect, then only the
  remote’s normal control address `<CONFIG_ADDR>` appears. It does not
  advertise as the HID identity and does not reconnect.
- Trace: `ofa/artifacts/laptop-bt-trace-laptop.txt`.

This confirms the gesture is received but LAPTOP does not enter the HID radio
pairing state.

## Strongest new evidence: slot 4 has no BLE-enabled state

The installed custom main firmware exposes a bounded `W 0B` label protocol.
It accepts only type-4 slots whose per-slot BLE bit is set:

```c
if (META[4 * slot + 0x17] >> 4 != 4) return 2;
if (!(*(volatile u32 *)(META + 8) & (1u << (slot - 1)))) return 2;
```

where `META = 0x20a2a0`; the mask is the u16 at `0x20a2a8`.

Read of DESKTOP labels succeeded:

```
FA MODE
FB SUBS
FC AUDIO
FD SPARE
FE RETURN
FF INFO
```

The first read of LAPTOP label `FA` returned `0b 02`, the handler’s
invalid/unsupported status. Its transaction journal is
`ofa/artifacts/laptop-page2-red-20260921.json`. Because slot 4 is
otherwise type 4, this is direct live evidence that mask bit 3 is clear.
No label/page write was made by that failed operation.

This mask also gates the existing touchscreen-to-HID path in the main image.
Direct `W 14` record creation/retyping made LAPTOP look type-4 in the device
list but did **not** run the stock provisioning transition that sets this bit.

## Stock code findings

Main image virtual base is `0x80de00`; the disassembly is in
`ofa/artifacts/tmp-rescue-20260921/ofa-thumb.txt`.

### Gesture path

The physical `Vol+` + `OK` gesture reaches `0x81d4dc`.

Relevant stock code, file offsets in parentheses:

```asm
81d52e  ldr r0, [20dbe4]
81d530  ldrb r0, [r0,#1]
81d532  cmp r0,#1
81d534  beq 81d54a              ; state == 1 => radio pairing path
81d536  ldr r0, [20b0b0]
81d538  ldrb r0,[r0,#0x15]      ; selected profile type
81d53a  cmp r0,#4
81d53c  beq 81d550              ; type 4 fallback => ordinary action
81d54a  bl  81e172              ; existing radio pairing routine
81d550  movs r0,#0
81d552  bl  81cb0c              ; ordinary key action
```

DESKTOP takes `0x81d54a`; LAPTOP takes the type-4 fallback. This exactly
explains the LED/advertising difference.

### Existing per-slot bit write

The only identified persistent write of the BLE mask is at `0x811954`:

```asm
811940  ldrb r0,[r4,#0x18]
811942  cmp r0,#3
811946  ldrb r1,[r7]           ; selected slot low nibble
811948  ldrh r0,[r7,#8]        ; META+8 BLE-enabled mask
81194a  and r1,#0xf
81194e  subs r1,#1
811950  lsls r5,r1
811952  orrs r0,r5
811954  strh r0,[r7,#8]
81195c  bl 0xb918              ; persist metadata object
```

This write occurs inside a stock state machine entered by dispatcher event 3;
the operation that puts the state byte at `r4+0x18` to 3 has not yet been
identified. It appears to be the original BLE provisioning workflow, not a
normal device-list `W 14` update.

### Existing radio routine

The radio routine starts at `0x81e172`. Routing LAPTOP there alone did not
start HID pairing, so an earlier/lower-level mask or controller-state gate
remains. It must be understood rather than bypassed blindly.

## Main-code variants already evaluated

None of these branch variants made LAPTOP blink. Do not repeat them.

1. `laptop-pairing-v1`: widened selected type comparison.
2. `laptop-pairing-v2`: removed a prior gesture early-exit gate.
3. `laptop-pairing-v3`: combined v1 and v2.
4. `laptop-ble-map-v1`: copied DESKTOP physical-key map to the earlier
   type-3 LAPTOP row. It is irrelevant after LAPTOP became type 4.
5. `laptop-pairing-v5` **currently installed**: one instruction at file
   offset `0xf73c`, replacing `08 d0` (`beq 0x81d550`) with `05 e0`
   (`b 0x81d54a`). This routes type-4 fallback into the stock radio routine.
   It still did not blink or advertise HID.

V5 artifact:

- `ofa/artifacts/laptop-pairing-v5/candidate-0.5.7-laptop-pairing-v5.bin`
- Candidate SHA-256:
  `020bebad1e8bfe2e7ea7ec2c094799a37b621775c0f66b49c94e07b003f81591`

## Precise question for Claude Fable

Find the stock, persistent provisioning transition that sets
`0x20a2a8` bit `(selected_slot - 1)` for a selected type-4 device, and explain
how to invoke or reproduce it for slot 4 **without deleting/replacing the
existing DESKTOP HID controller bond**.

Specifically determine:

1. What dispatcher event/payload and state transition causes `r4+0x18 == 3`
   in the `0x8118dc` state machine and reaches `0x811954`.
2. Whether that workflow provisions a per-slot software flag only, or writes a
   controller bond record that can support two primary HID hosts.
3. Why `0x81e172` exits without pairing when reached from LAPTOP under v5.
   Identify every BLE-mask/controller/bond gate it reaches.
4. Whether the RTL8762C controller/application stores only one HID peer/bond.
   If it does, state plainly that dual-host switching cannot be safely added by
   a narrow main-firmware branch patch.
5. If a code change is viable, give exact audited offsets/instructions and a
   proof plan that first preserves and verifies the DESKTOP bond.

## Constraints

- Do not web search.
- Use only committed artifacts and rescued files under
  `ofa/artifacts/tmp-rescue-20260921/`.
- Do not rebuild Blutter or Dart.
- Do not re-decompile the APK.
- Do not use `W 11` device deletion.
- Stop and report if the only remaining option is a speculative controller
  bond overwrite.

## Useful source files

- `ofa/CUSTOMIZATION-2026-09-20.md`
- `ofa/laptop_pairing_v5_firmware.py`
- `ofa/label_patch.c`
- `ofa/touch_pages.py`
- `ofa/artifacts/tmp-rescue-20260921/ofa-thumb.txt`
