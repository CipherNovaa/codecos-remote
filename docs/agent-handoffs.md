# Built with agents: the handoff trail

This project was built by the author working with three AI agents, each with a fixed
lane, passing work between them in written briefs. The briefs are kept in
[`handoffs/`](handoffs/), lightly scrubbed (names, addresses and personal context
removed; the engineering is untouched).

## Who did what

| Agent | Lane |
|---|---|
| **Claude** (Anthropic, Claude Code) | Ran the project: BLE pairing and the config-protocol work, the PC-side tools and the Control Center, the screensaver firmware, the briefs, and **read-only audits** of every firmware patch it did not write (byte diff, capstone disassembly against stock, unicorn emulation). Also wired the keys into the author's media player. |
| **Codex** (OpenAI) | The early firmware work: emulating the stock ARM code, the corrected key-matrix offsets, the BLE24 / custom-touch / channel-swap / Play-Pause patches, the e-paper font image, the PC Control Desk (`ofa_control.py`); later, authoring the touchscreen tap-vs-hold patches and their fix. |
| **Fable** (a Claude model used as a deep-analysis subagent) | Static reverse-engineering when the live evidence stopped making sense: the dual-host pairing answer, several of the screensaver root causes, and the real-code emulation harnesses in `ofa/re/` that caught a crash a stubbed audit had passed. |
| **The author** | Every physical step: holding the pairing chords, flashing, pulling batteries, reading the e-paper, and the final say on every flash. |

The rule throughout: **an agent that wrote a firmware patch does not audit it, and no
image is flashed without an offline checker passing and the author's go.**

## The documents

| File | From → to | What it shows |
|---|---|---|
| [`CODEX-BRIEF-touchscreen-ble.md`](handoffs/CODEX-BRIEF-touchscreen-ble.md) | Claude → Codex | One brief bundling four phases of remote-side work (touch keys over BLE, labels, font, logos) with a division of labour, defaults to proceed on, and what to report back. |
| [`CATCHUP-2026-09-20.md`](handoffs/CATCHUP-2026-09-20.md) | Claude → Codex | A mid-project status update: what the PC side had finished, so the firmware agent did not have to guess. |
| [`CLAUDE-FABLE-HANDOFF-2026-09-21.md`](handoffs/CLAUDE-FABLE-HANDOFF-2026-09-21.md) | Claude → Fable | Five precise questions about why a second computer would not pair, with every trace and candidate image listed. |
| [`FABLE-ANSWER-laptop-pairing-2026-09-21.md`](handoffs/FABLE-ANSWER-laptop-pairing-2026-09-21.md) | Fable → Claude | The answer: the "provisioned" bit is a receipt of a real bond, all five firmware candidates patched the wrong gate (v5 provably a no-op), and the fix needs no flash. |
| [`CLAUDE-FINAL-HANDOFF-2026-09-22.md`](handoffs/CLAUDE-FINAL-HANDOFF-2026-09-22.md) | Codex → Claude | Close-out of the pairing and key-map work, with evidence files for every write. |
| [`TOUCHSCREEN-HOLD-BRIEF.md`](handoffs/TOUCHSCREEN-HOLD-BRIEF.md) → [`TOUCHSCREEN-HOLD-HANDOFF.md`](handoffs/TOUCHSCREEN-HOLD-HANDOFF.md) | Claude → Codex | The tap-vs-hold design. The brief was superseded once measurement showed the screen MCU only reports completed taps, so the change had to span both chips. |
| [`TOUCH-REGRESSION-FIX-HANDOFF.md`](handoffs/TOUCH-REGRESSION-FIX-HANDOFF.md) | Claude → Codex, with Fable's report as appendix | Three regressions from that flash, root-caused by emulation, with the fix order fixed (screen first, recoverable; then main, no rollback). |

References in these files to "memory notes" or `[[...]]` links point to the agents'
persistent project notes, which are not part of this repo. Paths under `artifacts/`
are local, git-ignored files (built images, journals).

## The lessons that came out of it

- **Never stub the function under test.** A 44-case audit passed a screen image that
  crashed on every favourite tap, because it stubbed `COPY_KEY`. The real function is a
  three-argument `memmove`; the patch called it with two, so the size register held a
  function pointer and 4,581 bytes were copied over a 20-byte stack buffer. Fable's
  `emu_screen.py`, running the real stock code, reproduced it. Audits since run real code.
- **Ask the hardware.** The screensaver chase produced four confident wrong theories
  (a sleep hook, a flash "silicon ceiling", image size, a dispatch table) before an
  on-device register readback showed that no uploaded image had ever been committed.
  The readback was added precisely so the agents could see what the remote was doing.
- **Refuse to guess the one dangerous byte.** Fable found the missing OTA finalize
  command but declined to emit a guessed frame, because the commit step is the only
  one that can brick the screen. The image went on through the vendor app instead.
