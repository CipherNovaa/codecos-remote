# OFA URC7985 final handoff — 2026-09-22

## Completed result

The URC7985 now has two independently paired Bluetooth HID host profiles:

| Remote device | Purpose | Status |
|---|---|---|
| `DESKTOP` | Existing PC host | Verified working |
| `LAPTOP` | New laptop host | Verified working |

Selecting one profile activates that host and deactivates the other. The remote
supports one live HID link at a time, which is the intended behavior. Both
profiles now have the same physical-button HID behavior and the same two
touchscreen pages.

The device list is clean: `DESKTOP`, `TELEVISION`, `AMPLIFIER`, and one
`LAPTOP`. The two obsolete unpaired `LAPTOP` records were removed through the
official OFA app. The surviving real-bond record was renamed from `LAPTOP BT`
to `LAPTOP` through that app.

## Current remote state

- Remote: `URC7985 R00`
- Main firmware version reported by the remote: `0.5.7`
- E-paper firmware: `0.1.63`
- Main custom-touch build is installed and stable.
- The mgs-codec e-paper font is installed and displaying correctly.
- Normal management address: `<CONFIG_ADDR>`
- Existing DESKTOP HID identity on the PC: `<HID_ADDR_SLOT1>`

## Pairing conclusion

The useful model is the one from the Fable review:

- A type-4 device record alone does not make a Bluetooth host usable.
- The per-slot BLE/provisioned state is produced after a real stock pairing,
  rather than being a reliable flag to set in advance.
- The correct way to add LAPTOP was to create it through the OFA app as a
  Google TV Box / type-4 device, choose **Pair with Bluetooth Device**, then
  select that remote profile and hold **Vol+ + OK** until the blue LEDs blink.
- Pairing `OFA Pro Touch` on the laptop created a second valid host bond while
  preserving DESKTOP.

This was proven by switching both ways and testing inputs on both hosts.

## Touchscreen configuration

The final LAPTOP record occupies **slot 5**, type 4. It originally had no
touchscreen pages. It now matches DESKTOP:

1. Page 1: 16-key numeric layout (`layout fe`).
2. Page 2: six custom controls (`layout ff`):
   `MODE`, `SUBS`, `AUDIO`, `SPARE`, `RETURN`, `INFO`.

The numeric page was copied from DESKTOP using the audited page tool. Each
custom label was written individually and read back. All seven operations
verified their intended state and left the other profiles unchanged.

Relevant evidence:

- `artifacts/laptop-pages-after-stock-pair-20260922.json` — inventory before
  the LAPTOP page configuration; slot 5 had no pages.
- `artifacts/laptop-slot5-digits-apply-20260922.json` — numeric page write and
  read-back.
- `artifacts/laptop-slot5-label-{red,green,yellow,blue,return,info}-20260922.json`
  — individual label backups and read-backs.

No additional code change is required for LAPTOP touchscreen support.

## Channel Up / Channel Down correction

The printed Channel Up and Channel Down positions were reversed in the
type-4 HID matrix. The final corrected image swaps only those two type-4
matrix assignments, so both DESKTOP and LAPTOP now report:

- physical CH Up -> HID Channel Increment (`0x009c`)
- physical CH Down -> HID Channel Decrement (`0x009d`)

The candidate changed only the two matrix bytes plus the mandatory image
digest. It validated on the remote and normal boot was read back. the author
confirmed both physical buttons now operate in the correct direction.

Evidence:

- `artifacts/channel-swap-v1/manifest.json` — exact two-cell candidate scope.
- `artifacts/channel-swap-v1/flash-20260922-attempt-01.json` — successful
  validation and activation request.
- `artifacts/channel-swap-v1/read-after-flash-20260922.json` — normal-mode
  post-update identity read.

## Important historical finding

An experimental active-HID branch change caused repeated inputs whenever an
HID host profile became active. It affected both DESKTOP and LAPTOP. Restoring
the stable custom-touch v2 application image resolved the behavior; both hosts
were then tested successfully. Do not reintroduce that experimental branch.

## Diagnostics on the phone

Android Bluetooth HCI snoop logging is enabled at full level:

```text
settings global bluetooth_btsnooplogmode = full
```

Bluetooth was restarted after enabling it. This captures future Bluetooth
traffic for diagnosis without changing remote bonds or touchscreen settings.

## Existing deeper analysis

- `FABLE-ANSWER-laptop-pairing-2026-09-21.md` — static analysis of the pairing
  state and why manually forcing the old LAPTOP record was the wrong approach.
- `CLAUDE-FABLE-HANDOFF-2026-09-21.md` — prior technical handoff and addresses.
- `CUSTOMIZATION-2026-09-20.md` — custom-touch mapping, touchscreen labels,
  font work, and current customization notes.

## Current recommendation

Treat the setup as complete. If diagnostics are needed later, preserve the
phone's HCI snoop capture and use the saved page-operation JSON files as the
authoritative record of the current LAPTOP touchscreen state. Do not alter
the working host records merely to test provisioning theories.

## Final physical media-key and touchscreen-label update

The final type-4 image is
`artifacts/pause-playpause-v1/candidate-0.5.7-pause-playpause.bin`.
It retains the Channel Up/Down correction and changes the physical Pause
position from Consumer Pause (`0x00b1`) to Consumer Play/Pause (`0x00cd`).
Physical Play remains Consumer Play (`0x00b0`). The remote validated the
image before activation was requested. Since DESKTOP and LAPTOP are both
type 4, this behavior is shared by both profiles.

The second touchscreen page on both DESKTOP (slot 1) and LAPTOP (slot 5) now
uses these display labels, with their existing key identities preserved:

- `RETURN` -> `SLEEP`
- `SPARE` -> `CAST`
- `MODE` -> ` SCREEN MODE` (the leading space is intentional)
- `SUBS` -> `SUBTITLES`
- `AUDIO` -> `AUDIO TRACK`

Each of the eight label changes was read back and its two-page layout was
unchanged. Evidence is in
`artifacts/pause-playpause-v1/labels-v{1,2,3}-slot{1,5}-*.json`; the
pre-change inventory is
`artifacts/pause-playpause-v1/inspect-before-labels-20260922.json`.

## PC control handoff

`ofa_control.py` and `CONTROL-DESK.md` provide the maintained local PC control
surface. It wraps all proven configuration operations with timestamped
journals: inventory/pages, type-4 custom touchscreen labels, safe device-name
updates, duplicate menu-order cleanup, raw activity backup, HID inspection and
an isolated offline one-button remap builder. Main firmware starts as an
offline transfer plan and needs a typed acknowledgement before execution.

The Control Desk now also includes a standalone PC e-paper uploader. It
implements the recovered app packet stream and verifies the remote identity,
ATT size, and every acknowledgement. A 2026-09-22 direct reflash of the
mgs-codec v5 image completed with the expected `a1 02` start acknowledgement
and `a2 02` acknowledgements for all 1,412 data packets; its journal is in
`artifacts/control-desk/20260922T234802Z-screen-direct-execute/transfer.json`.
The app-assisted `screen stage IMAGE.bin` recovery route remains available.
Activity records are still exposed as lossless backups only because their
writer protocol has not been validated.
