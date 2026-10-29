# OFA URC7985 Control Desk

`ofa_control.py` is the local PC control surface for this remote. It uses the
project's tested BLE/configuration helpers and saves a new, timestamped journal
for every operation under `artifacts/control-desk/`. It does not need the OFA
phone app for supported operations and makes no network requests.

Run it from the repository root:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py --help
```

Running it without a subcommand offers a small guided menu. Start with:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py doctor
```

## Remote configuration

For status, device, page and label work: put the remote on its case, select a
non-Bluetooth device such as AMPLIFIER, turn Bluetooth off on the phone, and
hold **ACTIVITIES + DEVICES** while the command runs. The command verifies its
own readback before reporting success.

Read the entire current configuration:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py status
```

Change a touchscreen key label. Labels are ASCII and 1–15 characters. The
alias remains the same, so its HID output does not change:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py label \
  --slot 1 --key yellow --text 'AUDIO TRACK'
```

The supported custom aliases are `red`, `green`, `yellow`, `blue`, `return`,
and `info`. Slots 1 and 5 are DESKTOP and LAPTOP in the current configuration.
Reselect a profile after a page/label operation to refresh its display.

Touchscreen page commands retain a complete backup and verify that unrelated
records and pages do not change:

```sh
# Inspect pages
ofa/artifacts/venv/bin/python ofa/ofa_control.py pages inspect

# Preview copy of numeric pages from DESKTOP to slot 5
ofa/artifacts/venv/bin/python ofa/ofa_control.py pages enable \
  --slot 5 --from-slot 1 --pages digits --dry-run

# Make that backed-up, verified page write
ofa/artifacts/venv/bin/python ofa/ofa_control.py pages enable \
  --slot 5 --from-slot 1 --pages digits
```

## Devices and activities

List device records:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py devices list
```

Rename an existing device record without changing its slot, type, code or
profile. A readback verifies the selected record and that all other records are
unchanged:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py devices rename \
  --slot 5 --name LAPTOP
```

`devices dedupe` is read-only unless `--apply` is supplied. It only removes
duplicated menu-order entries; it never deletes a device record or bond.

`activities` creates a lossless backup of the remote's twelve raw activity
records. A PC-side activity writer has not been validated, so it is deliberately
not exposed: the records are opaque and an invented writer could erase IR or
activity state. This is the current safety boundary, recorded in the journal.

## Key outputs and main firmware

Inspect the HID map in any supported main image:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py keys inspect \
  ofa/artifacts/pause-playpause-v1/candidate-0.5.7-pause-playpause.bin
```

`keys patch` is an expert, **offline-only** builder. It assigns the named
physical type-4 button an unused Consumer-page pool entry and recalculates the
Realtek digest. Its manifest shows every changed byte. DESKTOP and LAPTOP share
the type-4 map, so the proposed change applies to both:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py keys patch \
  ofa/artifacts/pause-playpause-v1/candidate-0.5.7-pause-playpause.bin \
  --button Pause --usage 0x00cd
```

Capture live HID events with `keys capture`; it grabs only input devices named
`OFA Pro Touch` and usually requires `sudo`.

## Diagnostics

The `debug` commands are read-only replacements for the useful hardware
diagnostic parts of the app. They save the complete GATT capability capture or
the remote's advertisements without retaining unrelated nearby Bluetooth
devices:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py debug identity
ofa/artifacts/venv/bin/python ofa/ofa_control.py debug scan --seconds 30
```

Main firmware always starts as an offline plan. It performs no BLE operation:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py main-fw \
  CANDIDATE.bin --stock ofa/artifacts/ble24/stock-0.5.7.bin \
  --apk artifacts/apk/split_config.arm64_v8a.apk
```

An actual transfer requires both `--execute` and this exact acknowledgement:

```text
--confirm 'FLASH MAIN FIRMWARE'
```

The command still requires a successfully validated image before activation.
It has no automatic retries or resume. Review the generated plan and journal
before using the execution flag.

## E-paper firmware

`screen audit` runs the saved offline recovery analysis. `screen stage` remains
an app-assisted recovery option: it validates the exact packet stream offline,
copies an image to Android, opens the OFA app, and leaves file selection and
**Start Upgrade** visible for review.

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py screen stage \
  SCREEN-IMAGE.bin
```

Use `screen audit` to inspect an image without connecting to the remote:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py screen audit \
  MAIN-STOCK.bin SCREEN-IMAGE.bin
```

## Shortcut-logo PNG candidates

`screen icon` creates an offline candidate that replaces one of the seven
known fixed shortcut-logo allocations (`netflix`, `prime`, `youtube`, `hbo`,
`rakuten`, `disney`, or `paramount`). It preserves the existing descriptor,
dimensions, allocation, code, and fonts; the selected logo changes globally.
It accepts the stock v013f image or the installed font-only derivative.

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py screen icon \
  ofa/artifacts/screen-font-v5-22px-condensed/candidate-screen-font-DO-NOT-FLASH.bin \
  netflix MY-LOGO.png
```

The journal contains the candidate image, source conversion, before/after
previews, and an allowlist manifest. It is offline and does not transfer an
image. No generic favourite/heart icon, per-device logo, screensaver, or
runtime PNG-upload command has been identified yet.

## Direct e-paper uploader

The Control Desk now includes `screen direct plan`, which reproduces the
app's `57 A1` start and notification-paced `57 A2` packet stream without
opening Bluetooth:

```sh
ofa/artifacts/venv/bin/python ofa/ofa_control.py screen direct plan \
  SCREEN-IMAGE.bin
```

`screen direct execute` is the direct PC sender. It validates the normal
remote identity and negotiated ATT size, subscribes before sending data, and
requires the controller's exact acknowledgements: `a1 02` for start and `a2 02`
for each data packet. It retains a complete journal and never retries or
resumes. A known-good 180,608-byte mgs-codec v5 recovery round trip completed
on 2026-09-22: all 1,412 data packets were acknowledged. Execution requires:

```text
--confirm 'FLASH E-PAPER DIRECT'
```

Review the plan and use a new log directory before executing. A timeout or
unrecognized response stops the sender immediately; inspect its journal before
any further screen operation.
