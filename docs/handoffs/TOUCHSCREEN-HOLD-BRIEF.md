# TOUCHSCREEN-HOLD BRIEF — give touchscreen keys a real HOLD (foreground firmware)

*Handoff for a **foreground interactive** Claude session in `~/Projects/codec` (the background/auto-mode session blocks the `venv` RE toolchain). Written 2026-09-26.*

---

## Why

codec needs to tell **tap** from **hold** on the touchscreen keys. the author's locked design:

- **CAST** (Blue, scancode 195) — tap = "HOLD TO CAST" toast · **hold** = cast last/current to the TV.
- **RESUME** (spare-1, scancode 199) — tap = "HOLD TO RESUME LAST" toast · **hold** = resume last/next episode locally on the selected device.

Today a touchscreen key **cannot hold** — see the ground truth below. Physical keys (RW/FF, Guide, Back, Home) hold fine.

## Ground truth — this is measured, not assumed

**Runtime (codec `rk` debug log, this session):**
```
touchscreen RESUME  scan=199:  DOWN & UP at the SAME millisecond (0 ms, 0 ms, 75 ms)
physical    RW/FF   scan=114:  DOWN→UP = 44 ms, 211 ms, 239 ms  (real, grows with hold)
```
So the touchscreen fires **press+release as one packet**; codec never sees "still holding," and the 400 ms hold timer can't arm.

**Firmware root cause (`ofa/TOUCHSCREEN.md`, lines 49–51):** for a connected slot, file `0xd1f8` → `0x81d136`'s connected branch **"sends the selected pool index and then zero"** through the same report builder `0x81cb0c` the physical buttons use. That trailing zero **is** the synthetic release. Physical buttons share `0x81cb0c` but are driven by real hardware down/up, hence real timing.

## Goal

Firmware change so a touchscreen key sends the keycode on touch-**DOWN** and defers the **zero** to touch-**UP**. Then codec's existing 400 ms hold rig fires with **zero codec changes**.

---

## Answer this FIRST (it sets the scope) — disassemble the touch→HID path

**Does the e-paper screen emit separate touch-DOWN and touch-UP events, or a single "tap" event per touch?**

Disasm targets (VA = file offset + `0x80de00`; pinned bin `artifacts/ble24/stock-0.5.7.bin`):
- a55a framed-message parser **VA `0x81b812`** → dispatch **`0x81ad60`**.
- cmd **`0x43`** handler at file **`0xd140`**: slot = frame byte 6, logical key = byte 7, **"additional flag" = byte 8** (← inspect this: does it encode press vs release?).
- `0xd1ac` (BLE-enable bitmap / macro-override check) → `0xd1cc` (accepts logical keys `0x0d..0x16`) → `0xd1f8` → **`0x81d136`** (the "keycode then zero" branch to patch).

**Decision:**
- **Screen sends separate down/up** (e.g. byte 8 flag, or two `0x43`s) → **MAIN-FW-ONLY** patch: in `0x81d136`, emit the keycode on the down event and the zero on the up event, instead of both inline. Small, targeted.
- **Screen sends a single tap** → also needs a **SCREEN-FW** change (RT150T, *recoverable*) to report down-on-touch / up-on-release as a55a events; main fw then maps them. Bigger (two-MCU), but the screen is the low-brick-risk MCU we already have tooling for.

`check_touch_digits.py` modeled one screen event producing *both* press and release buffers, which hints at "single tap" — but confirm by disasm, don't trust the hint.

---

## Change design

- Keep report builder `0x81cb0c` (physical + touchscreen share it — good).
- Down → `0x81cb0c(keycode)`; Up → `0x81cb0c(0)`. The only change is **when** the zero is sent.
- Class of edit = the same additive/spare-region style the author has flashed safely (spare-keys, channel-swap, screensaver W0C).

## ⚠ Guardrails — do NOT reintroduce the "repeated-input" bug

`CLAUDE-FINAL-HANDOFF-2026-09-22.md` (§"Important historical finding"): an **active-HID branch caused repeated inputs whenever an HID host profile became active**, on BOTH hosts; the fix was reverting to the stable custom-touch v2 image. **The discrete keycode+0 tap IS that fix.** Making keys hold-capable re-enters that territory, so:

1. **Mandatory auto-release safety:** if no touch-UP arrives within a bound (e.g. 3–5 s) **or** the HID link drops mid-hold, the fw must force a zero report so a lost release can never leave a key stuck down. Test a mid-hold walk-out-of-range and a mid-hold disconnect.
2. **Alkalines only** — NiMH brownout independently causes repeated inputs ([[codec-desktop-bt-antenna]]).
3. Validate with the phone's HCI snoop log (already at full level) that one physical hold = exactly one down report + one up report (no repeats).

## Toolchain / artifacts

- venv (unicorn+capstone): `ofa/artifacts/venv/bin/python` — approve it live in the foreground session, or add allow-rule `Bash(ofa/artifacts/venv/bin/python:*)`.
- Analysis bin (pinned): `artifacts/ble24/stock-0.5.7.bin`.
- **Base to patch = the LIVE main fw = codecOS v16.0, SHA `957c9549`** (spare-keys + screensaver W0C + LED-off lineage). Confirm the base SHA with `identify()` before patching.
- Screen fw (only if the screen-side change is needed): the live screen image is the **screensaver-v15** lineage — confirm its version first; stock reference asset `assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin`.
- **Validation harness:** extend the `check_touch_digits.py` unicorn model — assert the report sequence is now `[keycode] … (gap) … [zero]` **gated on separate events**, and that the auto-release timeout emits a zero. Reproduce its bounded-exec pattern; fence all writes as prior builds did.

## Flash safety (unchanged rules)

Main MCU = **brick-risk, no rollback**; e-paper = recoverable. Flash via the OFA app Custom OTA. Preserve the **4 device bonds + all touchscreen pages**. **Never** touch the OTA-entry / recovery path (screen `0x1c5e0`/`0x1c294`; main recovery bytes byte-identical). Size constraints per prior flashes.

## codec side — ALREADY DONE (in the working tree)

The moment the firmware sends real down/up timing, hold works with **no codec change**:
- `appcontroller.cpp`: CAST(195) + RESUME(199) each arm `HoldCast`/`HoldResume` on press via the 400 ms `m_remoteHoldTimer`; `remoteHoldFired()` runs `remoteResumeLast(true/false)`; a sub-400ms release shows the hint toast.
- `resumeTarget()` / `remoteResumeLast(bool)` engine — live-validated vs the 918-row DB (finished X-Files S06E03 → S06E04; finished Sopranos S02E06 → S02E07).
- ⚠ **TEMP:** a `Logger::log("rk", …)` line at the top of `handleRemoteKey` logs raw key DOWN/UP timing (used to diagnose this). **Remove it once hold is verified.**
- These edits are the follow-up to commit `2e6f712` and may be uncommitted — check `git status` / `git log` in the foreground session.

## Acceptance test (after flash)

1. `debugLog` on. **Hold spare-1** → `rk` log shows `scan=199 DOWN` then `UP` **>400 ms later** → toast "NEXT ▸ …" + playback. **Tap** → DOWN/UP <400 ms → "HOLD TO RESUME LAST".
2. **Hold Blue (CAST)** → cast path (upstairs, with a TV).
3. 50+ presses + a mid-hold range walk-out + a mid-hold disconnect → **no repeated or stuck keys**.

## Fallback if the firmware route stalls

codec-side **double-tap** (tap = hint, tap-again within ~1.5 s = act) is reliable with the *current* firmware and needs no flash — a drop-in if the hold change proves too risky. Not built yet; ~15-min change.
