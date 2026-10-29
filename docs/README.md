# Docs

| File | What it is |
|---|---|
| [ROADMAP.md](ROADMAP.md) | What is done, what is next, the hard limits |
| [agent-handoffs.md](agent-handoffs.md) | How the agents split the work, and an index of the briefs |
| [phone-debug-mode.md](phone-debug-mode.md) | The official app's hidden Debug Mode, and pairing a second computer |
| [../hardware/bluetooth-antenna.md](../hardware/bluetooth-antenna.md) | The DIY quarter-wave antenna and the RSSI measurements |

## `notes/`: the engineering record, written during the work

| File | Covers |
|---|---|
| [BLE24-PATCH-LOG.md](notes/BLE24-PATCH-LOG.md) | The first main-firmware patch: corrected key-matrix offsets, the OTA preflight, the first flash |
| [TOUCHSCREEN.md](notes/TOUCHSCREEN.md) | How touchscreen digits were found to already reach Bluetooth, with no flash |
| [CUSTOMIZATION-2026-09-20.md](notes/CUSTOMIZATION-2026-09-20.md) | Custom-touch keys, the label protocol, the font work |
| [CONTROL-DESK.md](notes/CONTROL-DESK.md) | The `ofa_control.py` command-line desk |
| [SCREEN-RECOVERY.md](notes/SCREEN-RECOVERY.md), [RECOVERY-PATH-ANALYSIS.md](notes/RECOVERY-PATH-ANALYSIS.md) | E-paper firmware recovery paths |
| [SCREENSAVER-DESIGN.md](notes/SCREENSAVER-DESIGN.md), [SCREENSAVER-BUILD.md](notes/SCREENSAVER-BUILD.md) | The screensaver design and build steps |
| [SCREENSAVER-DEBUG-STATE.md](notes/SCREENSAVER-DEBUG-STATE.md) | The full screensaver debugging log, every address included |

These are working notes: dates, superseded theories and "next step" lists are left in
because the dead ends are part of the record. Where a note and the README disagree, the
README (and the code) is current.

## `handoffs/`

The briefs passed between Claude, Codex and Fable. See [agent-handoffs.md](agent-handoffs.md).
