# codecOS remote

Reverse-engineering a One For All URC7985 universal remote over Bluetooth, patching
both of its chips into custom firmware, and wrapping it all in a desktop app.

**Status:** working, in daily use. The custom firmware ("codecOS v16" on the main chip
plus a patched e-paper image) is flashed and hardware-verified, and the PC toolkit and
Control Center run on Linux. What's left is on the [roadmap](docs/ROADMAP.md): voice
search, TV control over Bluetooth, scene-style activities. This is a hobby project for
one model of remote; read the safety section before touching firmware.

<!-- MEDIA: hero photo of the URC7985 in hand, e-paper touchscreen lit and showing the custom page (SCREEN MODE / SUBTITLES / AUDIO TRACK / CAST / SLEEP in the pixel font). Natural light, screen readable. -->

## What it does

- **Every button over Bluetooth.** Stock firmware sends 9 to 12 of the 24 physical
  buttons as Bluetooth HID depending on profile; the rest go out as IR or nothing. The
  patched firmware sends all 24 as distinct HID keys to the PC.
- **Touchscreen keys the PC can use.** The e-paper touchscreen gets 6 named keys and
  10 spare keys that send HID (F14-F19, then keys no layout uses), each with a custom
  label you rename from the PC without reflashing.
- **Tap vs hold on every touchscreen key**, reported as real press/release edges like a
  physical button, so the PC side can give each key two actions.
- **Smaller fixes**: channel up/down un-swapped, Pause sends Play/Pause, and the blue
  light bar that flashed on every key press is switched off.
- **An e-paper screensaver.** Up to six of your own images in flash, shown at random or
  in order whenever the remote sleeps, and woken cleanly by a key press or pickup.
- **A pixel font** on the e-paper screen.
- **Two computers on one remote**, switched from the remote's Devices screen.
- **IR on command**: the PC can make the remote press a key on any IR device slot
  (amplifier volume, TV power) over Bluetooth.
- **OFA Remote Control Center**, a desktop app with 7 tabs: Connect, Screensaver,
  Labels, Lights, IR & Amp, Firmware, Logs/Verify.
- **A desktop Bluetooth antenna** made from 31 mm of coax: about 20 dB better at range.

<!-- MEDIA: screenshot of the Control Center on the Screensaver tab: an image dropped in, the live 1-bit preview beside it, and the 6-slot grid with thumbnails. -->

## Safety first: this can brick your remote

The main chip (Realtek RTL8762C) has **no dual-bank rollback**. A main image that does
not boot leaves you with a dead remote. The e-paper chip is recoverable through the
official app; the main chip is not. Everything here is built around that:

1. **Build offline.** Each builder reads *your* stock firmware, checks its SHA-256
   against the exact version it was written for, applies the patch, recomputes the
   Realtek image digest, and writes a `*-DO-NOT-FLASH.bin` plus a manifest of every
   changed byte. No Bluetooth involved.
2. **Check offline.** Every patch has a checker (`check_*.py`) that disassembles the
   changed code with capstone and runs it in unicorn against the real stock code,
   asserting the old behaviour still holds where it should.
3. **Flash through the gates.** `flash.py` makes an offline plan by default and only
   touches the radio with `--execute`. It then refuses to continue unless:
   - the image is on its **allowlist**: rebuilt from your stock image inside `flash.py`
     and byte-identical (an arbitrary `.bin` is refused);
   - the remote reports the expected model, OTA capabilities and app version, with no
     partial update pending;
   - the **battery is at 50 % or more** (use fresh alkalines: NiMH cells fool the gauge);
   - the remote **validates the whole image** (`10 03 01`) **before activation** is
     sent. A rejected image leaves the old firmware running.
   The "Unlikely Error" at activation is expected: the remote reboots and drops the link.
4. **Isolate the remote while flashing.** A second Bluetooth connection (a phone's HID
   auto-connect, the desktop's per-keypress reconnect) wedges the update. Unpair it
   from other devices and power off the PC's own HID link first.

The Control Center adds a typed confirmation, disables every other tab during a flash,
and only unlocks a candidate whose checker passed or whose SHA matches a pinned
recovery image.

**Never let the official app update firmware or "sync" your devices.** It overwrites
the custom images, and its device database drifts from what is on the remote.

The firmware itself is never in this repo; see [`ofa/artifacts/README.md`](ofa/artifacts/README.md).
Like an emulator that needs your own BIOS, you supply the stock images from your own
copy of the app.

## How it works

### 1. The config protocol, from a phone's Bluetooth log

The remote talks to its phone app over a vendor GATT service with **no
authentication**. Turning on Android's HCI snoop log, driving the app, and reading the
capture with `btmon` gave the channel: write characteristic `0x1121`, notify `0x1122`,
commands of the form `"W" + opcode`. The Flutter app's compiled Dart can't be
decompiled usefully, but its strings name the whole command set (`writeEInkDisplayInfo`,
`findMyRemote`, `deleteDevice`...), and diffing snoops mapped names to opcodes:

| Opcode | Meaning |
|---|---|
| `W 01` / `W 02` | identify / list device records |
| `W 14` | create or rename a device slot (the record's type nibble picks its HID key row) |
| `W 11 <slot>` | **delete** a slot. An early tool called it "prepare"; on the wrong slot it destroys the PC bond |
| `W 19` | menu order (how duplicate menu entries were removed without deleting anything) |
| `W 17` / `W 0B` | touchscreen pages / custom labels |
| `W 16 <slot> <key>` | make the remote press a key on a device slot, IR included |
| `W 09` / `W 36` | read / write the 8-byte settings struct (backlight, screen timeout, buzzer) |

"Find my remote" turned out to be the standard Immediate Alert service (`0x2a06`).

### 2. Pairing: the record that will not blink

Each device slot has its own Bluetooth identity derived from the factory address.
Only a device **created in the official app** will ever enter pairing; one created
over the config channel never does. And only a Fire TV-type device pairs cleanly. So
the recipe that worked: create a Fire TV device in the app, pair it, then flip its type
nibble to Google TV over the config channel. The HID descriptor is identical across
profiles, so the bond survives and the slot now uses the Google TV key row.

The remote's own Power key sends a real `KEY_POWER`, which shut the desktop down once.
`tools/early/70-ofa-remote.hwdb` remaps it to F13 for this device only.

### 3. The main firmware

The main image is a Realtek RTL8762C application (ARM Cortex-M4). Two things had to be
understood before anything could be changed:

- **Image integrity:** a SHA-256 over three regions of the image, stored inside it.
  Every builder recomputes it (`firmware.py`).
- **Transfer:** the OTA protocol encrypts 16-byte packets with AES-256-ECB. The key is
  read at run time from the `libRtkAesJni.so` in your copy of the app; it is never
  printed or stored.

The buttons are a 5×6 matrix per profile row; each cell indexes a pool of HID usages.
The first patch (BLE24, 57 bytes) filled the Google TV row's empty cells and repointed
unused pool entries, giving 24 distinct keys. Each later release was built on the
previous installed image, with its own builder and checker:

| Patch | Change |
|---|---|
| custom-touch (CT01) | colour/Return/Info touchscreen keys send F14-F19; adds the label protocol (+608 B) |
| channel swap | the two channel cells were printed in reverse (2 bytes + digest) |
| Pause → Play/Pause | one usage byte |
| spare keys | 10 renameable keys (52 bytes). The first build used Copy/Paste/Undo-style codes, which mean something to every app, so v2 moved them to Sun function keys and Japanese IME keys that a US layout ignores. F20 was skipped: xkb maps it to mic-mute |
| blue-LED-off ("codecOS v16") | the light bar turned out to be a plain GPIO LED driven straight from the key, IR and touch handlers, with no setting that controls it; NOP the four call sites, keep the pairing blink |
| tap-vs-hold, main side | 6 code bytes: send the keycode on a press frame and a release on a release frame |
| IR gate | a trampoline in verified-empty padding so a touchscreen IR key fires once, not on press and release |

The device still reports `0.5.7`. "codecOS v16" is a project name only.

### 4. The second chip: tap vs hold

The touchscreen is a separate MCU (Cortex-M0+) running LVGL, and it only reported
**completed taps**: a held key arrived as press and release in the same millisecond. So
tap-vs-hold needed both chips. The screen patch repoints the touch-key callbacks to
wrappers that report a press on `PRESSED` and a release on `RELEASED`. The full set of
four callbacks was found by **enumerating every caller of the screen's key-send
function**, not by guessing; that is how the third and fourth were caught.

It shipped with regressions, and fixing them produced the project's most useful rule.
The first fix crashed the screen on every favourite tap. The audit had passed because
it stubbed `COPY_KEY`, which is really a three-argument `memmove`; the patch called it
with two, so the size register held a function pointer and about 4.5 KB were copied over
a 20-byte stack buffer. An emulator that runs the **real** stock code (`ofa/re/`)
reproduced it at once. Audits since run real code, never stubs.

An earlier screen build also woke the remote over and over: widening the event filter
delivered LVGL's whole event stream (draw, refresh...), and the wrapper wrote screen
state on every one. Gate on the event code first.

### 5. The e-paper screensaver: four wrong theories

The design: a main-chip forwarder (`W 0C`) relays image data to a new screen command
that writes one of six 8 KB flash slots, and the screen blits a slot when it goes to
sleep. For two days nothing displayed, and every theory was wrong in an instructive way:

1. A sleep hook in the wrong place, then a hook using a Thumb-2 `b.w` the Cortex-M0+
   doesn't have: a HardFault and reset ~100 ms after every refresh.
2. The dispatch table entry was patched **one slot off**, so the command went to an
   error stub.
3. Images larger than stock seemed to be rejected by the bootloader.
4. The real cause: **the PC uploader never sent the OTA finalize step**, so no screen
   image had ever been committed. Every flash "succeeded" and the old image kept
   running. It also explains why none of the experiments bricked anything.

What broke the deadlock was observability: an **on-device register readback** smuggled
back through the main chip's version reply, which let the agents see command counters,
the slot map and battery voltage instead of guessing. The final image fits inside the
stock image size, draws by writing the framebuffer directly (bypassing LVGL), and was
flashed through the official app.

Two more findings: the "flash only takes two slots" ceiling was really **dropped
write-without-response frames** (fixed with auto-resend after the CRC check), and a key
press froze the panel on dense images because a partial e-ink refresh cannot erase a
full-contrast picture, so wake now forces a full refresh.

<!-- MEDIA: photo of the remote asleep on a table with a custom 1-bit screensaver image on the e-paper (use an image you made or own). A before/after pair with the stock sleep screen works well. -->

### 6. The Control Center

A PyQt6 app that **imports the verified engines instead of reimplementing them**.
`qasync` puts Qt's event loop and asyncio on one loop, so Bluetooth runs in-process
without threads; flashing, checkers and loggers run as `QProcess`es. One device lock
serialises all access to the remote, and every write is journalled to a timestamped
folder. Hard-won gotchas are recorded in the code: a `QProcess` parented to a plain Python `QObject` aborts under PyQt6;
loggers launched with `pkexec` run as root and need `pkexec` to stop; and a link probe
must treat a successful identify as "linked", because the screen-relay reads flake
without meaning the link is down.

### 7. The antenna

The desktop's Bluetooth jacks were bare. A quarter wave (31 mm) of coax centre
conductor, coiled onto the jack's centre pin with no solder, took the far side of the
room from about -95 dBm with drops to about -74 dBm with none. Details and RSSI traps in
[`hardware/bluetooth-antenna.md`](hardware/bluetooth-antenna.md).

<!-- MEDIA: photo of the DIY antenna standing out of the rear I/O RP-SMA jack. -->

## Workflow for this Project

- **Three agents, fixed lanes, written handoffs.** Claude ran the project and the PC
  side, Codex wrote most of the main-chip patches, and Fable (a Claude model used as a
  deep-analysis subagent) did static reverse-engineering when evidence stopped making
  sense. The briefs between them are in [`docs/handoffs/`](docs/handoffs/), indexed in
  [`docs/agent-handoffs.md`](docs/agent-handoffs.md).
- **The author of a patch never audits it.** Codex's firmware was audited read-only by
  Claude (byte diff, capstone against stock, unicorn emulation) before the author
  flashed it.
- **A checker per patch, so an agent can verify without hardware.** Every builder has
  an offline `check_*.py`; `flash.py` will only flash an image it can rebuild
  byte-for-byte from the user's stock firmware.
- **Instruments built so the agent could see.** The screensaver went nowhere until an
  on-device register readback was added for the agents to read; a press/release
  logger did the same for tap-vs-hold.
- **Knowing when not to act.** Fable found the missing OTA finalize command but refused
  to emit a guessed frame for the one step that can brick the screen; the image went
  through the vendor app instead.

## Repository layout

```
ofa/                    the toolkit: one flat directory, the tools import each other
  ofa_control.py        command-line Control Desk (status, labels, pages, keys, firmware, screen)
  control_center/       the PyQt6 desktop app       (launch: ofa/control_center_app)
  flash.py, dfu.py, preflight.py        main-chip OTA flasher and its pieces
  *_firmware.py, touchscreen_hold_*.py  offline patch builders (+ .c/.S/.ld patch sources)
  check_*.py            one offline checker per patch
  codec_screensaver.py  screensaver slots, rotation, status readback
  codec_led.py          backlight / screen / buzzer settings
  labels.py, touch_pages.py, device_order.py, rebalance_spare_pages.py   config-channel tools
  screen_fonts.py, screen_images.py, screen_direct.py                    e-paper assets and uploads
  re/                   real-code emulation harnesses used for audits
  ofa_identity.py       the remote's Bluetooth address (from env/config)
  artifacts/            your local workspace: stock images, builds, journals (git-ignored)
integrations/codec/     amp/TV IR over Bluetooth for the author's media player
tools/radio/            RSSI and range loggers
tools/early/            the first-week reverse-engineering scripts, kept as found
hardware/               the antenna
docs/                   design notes, debug logs, handoffs, roadmap
```

The `codec_` prefix on some tools is historical: the remote was built to drive codec,
the author's media player. They work without it.

## Requirements

- Linux with BlueZ (the RSSI tools use the BlueZ mgmt socket and need root).
- Python 3.11+ and the packages in `requirements.txt`.
- For rebuilding patches: `clang`, `ld.lld` and `llvm-objcopy` (ARM targets).
- A One For All URC7985 with main firmware 0.5.7 and e-paper firmware v013f, and your
  own copy of the official app (for the stock images and, for flashing, its AES library).

## Install

```sh
git clone https://github.com/CipherNovaa/codecos-remote.git && cd codecos-remote
python3 -m venv ofa/artifacts/venv
ofa/artifacts/venv/bin/pip install -r requirements.txt
mkdir -p ~/.config/codecos-remote
cp config.example.json ~/.config/codecos-remote/config.json   # then set "addr"
```

The remote's management address is what it advertises as "OFA Smart Control" while
ACTIVITIES and DEVICES are held (`bluetoothctl scan le`). You can also export
`OFA_ADDR` instead of using the config file.

## Usage

Config-channel work needs: the remote's phone app disconnected (phone Bluetooth off),
an IR device selected on the remote so it drops its PC link, and **ACTIVITIES + DEVICES
held until the LEDs blink**. The hold expires after a few writes; hold it again.

```sh
ofa/control_center_app                                   # the desktop app

PY=ofa/artifacts/venv/bin/python
$PY ofa/ofa_control.py doctor                            # check the install
$PY ofa/ofa_control.py status                            # devices + touchscreen pages
$PY ofa/labels.py set --slot 1 --key spare1 --text "TRAILER" \
    --backup ofa/artifacts/labels/spare1-before.json --apply   # backed up + read back
ofa/codec_screensaver set my-art.png --slot 0 --mode cover --method threshold
ofa/codec_screensaver cycle random
$PY ofa/codec_led.py backlight --level 1                 # dim the key backlight

# firmware: build, check, plan, then (only then) flash
$PY ofa/blue_led_off_firmware.py --stock ofa/artifacts/ble24/stock-0.5.7.bin
$PY ofa/check_blue_led_off.py
$PY ofa/flash.py ofa/artifacts/codecos-v16/main-codecos-v16-DO-NOT-FLASH.bin \
    --stock ofa/artifacts/ble24/stock-0.5.7.bin --apk ofa/artifacts/apk/split_config.arm64_v8a.apk
# add --execute --log <new file> only after reading the safety section

sudo OFA_HID_ADDR=XX:XX:XX:XX:XX:XX python3 tools/radio/ofa_range.py 180   # RSSI per keypress
```

Builders depend on earlier builds (each patch sits on the previously installed image);
each one's `--help` names its base, and it refuses a base whose SHA does not match.
The detailed history of every patch and flash is in [`docs/notes/`](docs/notes/).

No screensaver images ship with this repo. Use your own.

## Credits and licence

- Tiqiaa Tview USB IR protocol (used by `tools/early/tview_rx.py` to prove which keys
  were going out as Sony IR): from XenRE's reverse-engineering,
  [gitlab.com/XenRE/tiqiaa-usb-ir](https://gitlab.com/XenRE/tiqiaa-usb-ir). The code
  here is a fresh implementation.
- One For All and URC7985 are trademarks of Universal Electronics Inc. This project is
  not affiliated with or endorsed by them, and ships none of their firmware or software.

MIT licence, see [LICENSE](LICENSE).
