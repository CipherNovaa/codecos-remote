# Screensaver firmware — build & flash (do this in a FOREGROUND interactive Claude session)

**Why a foreground session:** the background/auto-mode agent's safety layer blocks the
firmware toolchain. In an interactive `claude` session YOU approve each step live — that
is the proper authorization for building + flashing brick-capable firmware.

## 1. Start the session
```
cd ~/Projects/codec && claude
```
Optional, so you don't re-approve the builder each run — inside that session:
```
/permissions   ->  Allow  ->  Bash(~/Projects/codec/ofa/artifacts/venv/bin/python:*)
```

## 2. Give it this prompt
> Build the OFA e-paper screensaver firmware (multi-slot Design A). Read
> `ofa/SCREENSAVER-DESIGN.md` and the `codec-remote-future-directions` memory note
> for the full spec + multi-slot params. Base = the installed mgs-font screen image
> (SHA `03ea3242…`, 180608 B). Model the builder on `spare_keys_firmware.py` /
> `custom_firmware.py` (main additive + Realtek digest recompute) and
> `check_screen_recovery.py` (unicorn harness). Assemble → capstone self-check →
> unicorn emulate-validate (multi-slot page builder, cmd-0x50 receive→per-slot flash +
> clear + mode, idle/restore hooks, main W1A forward) → and ONLY on full pass emit
> `artifacts/screensaver-v1/{screen,main}-*-DO-NOT-FLASH.bin` + `manifest.json`. Keep the
> recovery path `0x40/0x46/0x47/0x48` byte-identical. Do NOT flash — I'll review first.

## 3. Before flashing — checklist
- Fresh **ALKALINE** batteries in the remote (NiMH brown out mid-write → corrupt flash).
- Remote on its USB case; **phone Bluetooth OFF**; select **AMPLIFIER** (drops the HID link);
  hold **ACTIVITIES+DEVICES** for the config channel.
- Review the validation output + `manifest.json`: recovery path unchanged, only intended bytes moved.

## 4. Flash order (validate-before-activate)
0. **Zero-risk size probe:** reflash the CURRENT image padded with +8 KB of `0xFF` — proves the
   bootloader accepts the larger image. (Screen MCU is recoverable, proven round-trip.)
1. **Screen candidate** — direct-PC uploader (`ofa_control.py screen direct execute` / `screen_direct.py`).
2. **Main candidate** — `ofa_control.py main-fw` / `flash.py` (`10 03 01` validation before
   activation; the activation reset drops GATT with "Unlikely Error" = EXPECTED success).
   Main has NO rollback → this is the careful one, but it's the proven additive/digest type
   (flashed safely 5× this project).

## 5. After flashing — set your screensaver (no reflash ever again)
```
ofa/codec_screensaver set ~/screensaver-finalists/image-a.png --mode cover --method threshold --show
```
Repeat for `image-b.jpg`, `image-c.png`, `photo-art.jpg` — flip on the real panel, crown the
winner. Load several into slots and they shuffle on idle. Change the image anytime with the
same one-line command — that's the whole point of Design A.
