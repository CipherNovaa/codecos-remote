# OFA URC7985 remote toolkit

Updated 2026-09-20. **The main CT01 firmware is installed,
boots, and accepts custom touchscreen labels.** DESKTOP now has its original
numeric page plus one page containing MODE/SUBS/AUDIO/SPARE/RETURN/INFO. The
six actions map to F14–F19, verified by live press/release capture. the author confirmed
the labels display correctly and the combined page 2 looks good.
The mgs-codec e-paper font image is installed; its direct-PC recovery upload was verified on 2026-09-22.

For day-to-day direct PC control, start with
[CONTROL-DESK.md](CONTROL-DESK.md) and run `ofa_control.py doctor`. It combines
the backed-up touchscreen/device tools, HID-map inspection/remap builder,
main-firmware planner, diagnostic capture and screen-recovery audit into one
local CLI with timestamped journals.

Start with [CUSTOMIZATION-2026-09-20.md](CUSTOMIZATION-2026-09-20.md) for the
current state, Claude's key/asset interface, label CLI and remaining screen
recovery limitation. [TOUCHSCREEN.md](TOUCHSCREEN.md) retains the original
stock-digit investigation. The sections below document the original BLE24
patch and its history; old device names/restore procedures are not current.

For the custom MGS font, [SCREEN-RECOVERY.md](SCREEN-RECOVERY.md) records the
latest work: corrected font/wrapping candidate, native-size page preview,
confirmed screen version `013f`, and the verified direct-PC recovery path.
The older `screen-font-v1` candidate is superseded and must not be installed.

Current devices: DESKTOP slot 1/type 4 (keep its `…7A` bond), AMPLIFIER slot 2,
TELEVISION slot 3, LAPTOP slot 5/type 4. No app sync or slot recreation is needed.

the author wants all main controls over Bluetooth for local codec playback. The
candidate, stock image, manifest and live probe captures are saved locally in
[`artifacts/ble24/`](artifacts/ble24/), which is ignored by Git. The APK and
firmware are user-supplied; they are not bundled with these tools.

## Current result and approval boundary

The candidate changes **57 bytes** in stock firmware 0.5.7: Google TV/type-4
mapping entries, unused HID usage entries, the keyboard Power usage to F13,
and the image SHA-256. It gives 24 main buttons distinct reports, including
separate Play and Pause. Activities and Devices remain local setup controls.
The other three BLE profiles retain their existing rows and HID usages.

The update, normal boot and expanded physical button map now work on the
hardware. Key-mover, activity and volume-lock handling can precede the BLE
lookup; the physical verification does not cover every such combination.
Codec bindings and Hyprland media-key routing have not been changed.

The working CODEC identity is slot 1 / type 4 / `<HID_ADDR_SLOT1>`.
The latest handoff also lists BRAVIA in slot 3 and CODEC CAST in slot 5.
Claude recovered pairing by pairing an app-created Fire/type-3 device first,
then changing its record to type 4 while retaining the bond. Do not delete or
recreate working slot 1. The app and remote databases have diverged; app sync
previously overwrote working state. `W 11 <slot>` **deletes** a slot, despite
the old cache tool calling it "prepare." Historical restore captures and
`restore_profiles.py` describe the earlier test arrangement, not today's layout.

The user's handoff reserved firmware flashing for a separate go. the author approved
after confirming that Sony/type-1 TV profiles retain their mappings. Before
that approval, the following non-flashing steps were performed:

- Read identification, battery, OTA capabilities and current image version.
- Temporarily enter the dedicated OTA environment using FFD1 = `01`.
- Query APP image information using control point `06 93 27`.
- Request ordinary reset with `05` and verify normal services returned.

`flash.py` defaults to an offline plan; `--execute` actually flashes and activates.
The approved run is recorded in `artifacts/ble24/flash-attempt-01.json`:

- Sep 19 18:25:21 local time: Start DFU accepted (`10 01 01`).
- 18:26:28: all 104128 data bytes submitted; validation requested.
- 18:27:32: **remote validation succeeded (`10 03 01`)**, then activation `04`.
- Activation interrupted the GATT response with Unlikely Error, as reset did
  in preflight. A fresh connection confirmed normal services and identity.
- `device-after-flash.json` records the successful post-activation connection.
- `restore-profiles.json` confirms CODEC slot 1 restored to type 4 / code 270b /
  profile 05; FIRE slot 4 restored to type 3 / code 2707 / profile 01. Both Sony
  slot records were compared before/after and are byte-for-byte unchanged.

The normal firmware version still reads 0.5.7 because this patch preserves it.
The subsequent live physical-button verification supplies direct evidence of
the expanded map.

## Live update preflight

Normal configuration radio: `<CONFIG_ADDR>`, model `URC7985 R00`, firmware
`0.5.7`, battery **82%** on the latest direct read after replacement/charging.
Phone Bluetooth must be off and the remote in Activities + Devices mode.

FFF1: `05 01 00 07 00 08 00 00 f0 0f 00 00`:

- RTL8762C (IC 5), OTA protocol version 1, secure version 0.
- Buffer checking supported, AES transfer encryption enabled for all blocks.
- Maximum buffer size 2048; standalone image indicators, no dual-bank rollback.
- APP version FFD4: `00 50 00 38` (`0x38005000`).

The normal-mode DFU query returned `10 06 09`: system-state error. It was **not**
evidence of an AES failure. The APK enters a dedicated OTA mode first.

Successful live preflight at 2026-09-20 01:07 UTC / Sep 19 18:07 local time:

1. Write `01` without response to normal-mode FFD1.
2. Scan for manufacturer ID `0x005d`, payload `<CONFIG_ADDR>`.
   Target advertised **BeeTgt**, transient address `<transient-ota-addr>`.
   Match the manufacturer payload, not a hard-coded transient address.
3. OTA target exposed only GAP, GATT and DFU service
   `00006287-3c17-d293-8e48-14fe2e4da212`.
4. Subscribe to `00006487-3c17-d293-8e48-14fe2e4da212`; write `06 93 27`.
   Reply: **`10 06 01 00 50 00 38 00 00 00 00`**: success, expected APP version,
   current update offset 0, no partial transfer reported.
5. Write `05`. GATT returned Unlikely Error as the link reset. A separate
   `read_device.py` connection confirmed normal services and firmware 0.5.7.

Captures: `ofa-ota-preflight-live.json`, `ofa-device-fresh-battery.json`,
`ofa-device-after-preflight.json` inside the local bundle. Mode entry/reset was
verified before flashing; the live transfer and normal boot are recorded above.

## Corrected firmware tables

Claude's row offsets were **two bytes early**. The first two apparent dense
keyboard rows are physical and logical/IR key maps, not extra BLE profiles.
All offsets below are absolute file offsets in the exact saved 0.5.7 asset.

| Data | Offset |
| --- | --- |
| Physical key IDs, 30 cells | `0x1636a` |
| Logical/IR key IDs, 30 cells | `0x16388` |
| Type 1 / Sony BLE row | `0x163a6` |
| Type 2 / Apple-like row | `0x163c4` |
| Type 3 / Fire row | `0x163e2` |
| Type 4 / Google TV row | `0x16400` |
| Usage pool including index-0 release sentinel | `0x1641e` |
| First actual usage, index 1 | `0x16420` |

Each matrix is 5 rows × 6 columns. ARM Thumb lookup at VA `0x81d42e`
(file `0xf62e`) computes `row * 6 + column`. It extracts the slot's type from
RAM `0x20a2a0 + 4*slot + 0x17`, and chooses one of the four rows. File-to-VA
translation adds `0x80de00`. Literal at file `0xfa00` points to the Sony row.

Sender at VA `0x81cb0c` (file `0xed0c`) indexes the pool through the pointer at
file `0xf100`. Indices 1–32 select keyboard page 7; indices 33+ select consumer
page C. Zero is a release/no-mapping sentinel. Dispatch at file `0xfa82`
routes nonzero assignments into BLE, otherwise toward IR. Activities/Devices
have earlier local handlers; their cells are not modified.

Candidate main-button assignments:

| Button | Matrix cell | HID page:usage |
| --- | --- | --- |
| Play | 1 | `0c:00b0` |
| OK | 2 | `0c:0041` |
| Record | 4 | `0c:00b2` |
| Mic | 6 | `0c:0221` |
| Stop | 7 | `0c:00b7` |
| Up | 8 | `0c:0042` |
| Mute | 9 | `0c:00e2` |
| Fast-forward | 10 | `0c:00b3` |
| Channel down | 11 | `0c:009d` |
| Back | 12 | `0c:0224` |
| Rewind | 13 | `0c:00b4` |
| Guide | 14 | `0c:008d` |
| Right | 15 | `0c:0045` |
| Channel up | 16 | `0c:009c` |
| Volume up/down | 18 / 19 | `0c:00e9` / `0c:00ea` |
| Down | 20 | `0c:0043` |
| Home | 21 | `0c:0223` |
| Power → F13 | 23 | `07:0068` |
| Left | 24 | `0c:0044` |
| Pause | 25 | `0c:00b1` |
| Apps | 26 | `0c:01a2` |
| Menu | 27 | `0c:0040` |
| Input | 29 | `0c:0089` |

Linux maps consumer `008d` to KEY_PROGRAM, `0089` to KEY_TV, `01a2` to
KEY_APPSELECT and `00b7` to KEY_STOPCD. Use the scan usage and physical label
when testing; the Linux names are not always the printed remote labels.

## Image integrity and transport

Stock SHA-256:
`2802d95568ecd78fbe8ec318508d6e6e807b8fb70ff15ee52698dd31239b5f47`

Candidate SHA-256:
`f859b1985d5bd9cda9f9a454bda9a0617aaf6553efc902997046895241148176`

File size 104652. The first 512 bytes are the MP header and are not transmitted.
The actual image has a 1024-byte header and 103116-byte payload; APP ID `2793`,
IC 5, secure version 0, flags `0101`, CRC field 0 (SHA-256 mode). Executable
content is plain Thumb; transfer encryption is a separate mechanism.

For `image = file[512:]`, Realtek hashes
`image[12:372] + image[404:752] + image[1008:]` and stores the digest at
`image[372:404]`. This reproduces the hashes in **all 21 stock APK assets**.
Image signatures are not populated in this asset, but that alone does not
establish what checks the installed bootloader will enforce.

The app initializes native AES with `aesInit(3, null)`: default AES-256 ECB key
inside its own `libRtkAesJni.so`. The tool extracts it from the user's split APK
only after pinning the library SHA-256. No key is printed or bundled. Six test
blocks match the actual AArch64 native library executed in Unicorn.

Prepared ordinary (non-buffer-check) transfer:

1. Enter OTA, confirm version `38005000` and update offset 0.
2. Start DFU: opcode 01 + encrypted 12-byte image control header + 4 zero bytes.
3. Receive image: `02 93 27 0c 00 00 00` (APP, offset 12).
4. Send remaining 104128 bytes as 6508 separately encrypted 16-byte packets to
   `00006387-3c17-d293-8e48-14fe2e4da212`, write without response.
5. Validate `03 93 27`; require success notification `10 03 01`.
6. Only then send activation/reset `04`; separately verify boot and keys.

The script rejects wrong assets, changed capabilities, low reported battery,
unexpected versions, existing partial transfers, and unsuccessful replies.
It does not automatically retry, resume, reset or activate on transfer failure.
The first experimental transfer succeeded. Failure handling is checked in a
simulated receiver; recovery after a bad boot remains unproven.

## Checks and local commands

Run from the codec repository root. `artifacts/venv` is an isolated environment
with Bleak and Unicorn, using the host's cryptography package. Recreate if needed:

```sh
python3 -m venv --system-site-packages ofa/artifacts/venv
ofa/artifacts/venv/bin/python -m pip install bleak==3.0.2 unicorn cryptography
```

Offline checks (all passed):

```sh
ofa/artifacts/venv/bin/python ofa/check_firmware.py artifacts/apk/x/assets
ofa/artifacts/venv/bin/python ofa/check_transport.py artifacts/apk/split_config.arm64_v8a.apk ofa/artifacts/ble24/candidate-0.5.7-ble24.bin
ofa/artifacts/venv/bin/python ofa/check_flash.py artifacts/apk/split_config.arm64_v8a.apk ofa/artifacts/ble24/candidate-0.5.7-ble24.bin
```

- 720 executions of the actual ARM matrix lookup, covering all cells, four
  profiles, three device slots, stock and candidate.
- Stock digest reproduction, corruption/length rejection, exact version
  pinning, 24 distinct usages, and unchanged other-profile behavior.
- Native APK encryption matches; simulated receiver reconstructs the entire
  image exactly, including the control header and final packet.
- Partial update, start rejection, disconnect, asynchronous error and failed
  validation all prevent activation in the simulated receiver.

Offline transfer plan, **no Bluetooth or flash**:

```sh
ofa/artifacts/venv/bin/python ofa/flash.py ofa/artifacts/ble24/candidate-0.5.7-ble24.bin --stock ofa/artifacts/ble24/stock-0.5.7.bin --apk artifacts/apk/split_config.arm64_v8a.apk
```

After explicit owner approval, the same command with `--execute --log NEW_PATH`
will perform the transfer. Keep the remote close to the PC antenna, phone
Bluetooth off, batteries charged, and do not interrupt the transfer. The
`--already-ota` option bypasses normal-mode reads and is only for a separately
confirmed matching OTA advertisement; it still refuses nonzero update offsets.

## Recovery limits and next work

The stock APP image is saved; normal-to-OTA entry and OTA-to-normal reset work.
Neither fact guarantees recovery from a candidate that fails to boot. The
capabilities report no dual-bank rollback. A failed boot could require a wired
programmer or leave this thrift remote unusable. No physical recovery path has
been established. The flasher changes only APP, not bootloader or ROM patch.

Next work after the successful transfer:

1. Normal boot is confirmed. Firmware text/version still says 0.5.7; it is not
   evidence that the patch is running. Newly reporting buttons are the live test.
2. CODEC slot 1 is restored to type 4, code `270b`, profile `05`; FIRE slot 4 to
   type 3, code `2707`, profile `01`. Handoff CREATE-form commands used, without prep:
   `W 14 01 40 80 27 0b 00 05 CODEC` and
   `W 14 04 30 80 27 07 00 01 FIRE`, names padded to 16 bytes.
   Device-list captures preserve duplicate records left by prior experiments;
   do not delete/rewrite unknown entries based only on their position.
3. Select CODEC and use Vol+ + OK for HID pairing if needed. Slot 1 identity is
   `<HID_ADDR_SLOT1>`. Do not delete unrelated existing bonds.
4. Test every button under an exclusive input grab. Keep the installed Power
   → F13 hwdb guard; new firmware also maps Power to F13.
5. Implement codec input bindings and resolve Hyprland's global media-key grab.
   Test local playback and bed range, including reconnect and hold behavior.
6. Delete experimental spare devices through the app once their role is clear;
   the config delete opcode has not been established.

## Evidence sources

- User APK assets and decompiled updater: `Q1/c.java`, `C1/b.java`, `C1/c.java`,
  `A1/h.java`, `A1/o.java`, and the native AES library. Temporary decompilation:
  `/tmp/ofa-jadx`; source APKs remain in `artifacts/apk/`.
- [Realtek RTL8762C OTA User Manual](https://www.realmcu.com/img/ipd/en_638290111802009694.pdf),
  image format, staging/copy modes and OTA protocol.
- [Vendor SDK SHA implementation (public mirror)](https://github.com/tropicalshrimph/rtl8762c-sdk-gcc/blob/main/src/flash/silent_dfu_flash.c).
- [Vendor SDK DFU status definitions (public mirror)](https://github.com/tropicalshrimph/rtl8762c-sdk-gcc/blob/main/inc/bluetooth/profile/server/rcu_dfu_service.h).
- [Linux HID input mappings](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-input.c).

The SDK mirror documents a related implementation, not a complete source release
for this OFA product. APK behavior, exact firmware execution and live captures
are identified separately above so that assumptions are not mistaken for proof.
