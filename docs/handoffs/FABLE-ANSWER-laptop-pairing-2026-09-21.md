# URC7985 LAPTOP dual-host — analysis answer (Fable, 2026-09-21)

Static RE only. No hardware touched, nothing flashed. Image = `artifacts/ble24/stock-0.5.7.bin`
(stock 0.5.7 asset; the installed image is the byte-identical custom-touch-v2 for these routines).
vaddr = fileoff + 0x80DE00. Every address below is read from the bytes; ROM/patch calls (targets
< 0x80DE00) are identified by call arity + SDK parameter-ID/length signatures.

## TL;DR
- **The `META+8` provisioned bit is a RECEIPT of a completed bond, not a precondition you flip to
  enable pairing.** The only writer that SETS it is `0x811954`, in the pairing-completion handler,
  and only when a real SMP bond finishes with advertising-mode == 3. **No stock config opcode sets it.**
- **Every laptop-pairing firmware candidate v1..v5 was structurally incapable of working** — they
  patch the wrong gate. v5 is a provable no-op (below).
- **Slot 4's failure is a provisioning-trigger problem, not a bond-capacity problem and not the
  gesture gate.** Selecting an unprovisioned slot never brings the radio up, so Vol+ + OK has no
  radio to pair on.
- **The correct, non-destructive fix needs NO flash:** drive slot 4 through the stock provisioning
  path the same way DESKTOP was done (app-register + pair), which sets the bit via a genuine bond.
- **Dual-host is architecturally supported and nothing in the pairing path clears an existing bond.**
  The single unproven number is the ROM's `max_le_paired_device` default, which is NOT in this image
  and can only be read by one live test. If it were 1, pairing slot 4 would LRU-evict slot 1.
  **This is not a speculative bond overwrite — do not attempt one.**

---

## Q1 — What causes `[ctx+0x18] (0x20b0c8) == 3` and reaches `0x811954`?
`0x20b0c8` is the **advertising-mode index**, written by the mode dispatcher `0x812752` to equal its
own jump-table case number (tbb table @ `0x81279a`; case 3 tail sets the byte to 3 at `0x812a60`).
Case 3 is invoked as `dispatcher(3)` from `0x811dbc`, inside the **GAP connection-event handler
`0x811cbe`**, when the live link reaches connection sub-state 2 (`0x811d8e cmp r2,#2; beq 0x811dbc`).

So `0x20b0c8 == 3` is not a payload you send — it is the state the firmware is in **while a host is
mid-bond**. The full chain that ends at `0x811954`:

  enter-pairing (event 0x41) → activation `0x81d91e` → ROM bondable advertising as identity …:7D
  → remote host runs SMP → GAP connect event → `0x811cbe` (sub-state 2) → `dispatcher(3)` sets mode 3
  → on bond success the completion handler `0x81189c`/`0x8118dc` (arg r1==1, status r5==0) with
     mode==3 ORs `META+8` bit(slot-1) at **`0x811954`** and `SAVE(0x20a2a0,4,0x60)`.

There is **no stock config-channel command anywhere in this path.** The bit is set only by finishing
a real bond.

## Q2 — Does that workflow set a software flag only, or write a controller bond record for two hosts?
Both, in two layers:
- **App layer (software flag):** `META+8` (`0x20a2a8`) is a per-slot bitmask, one bit per slot. The
  app is clearly designed to hold several provisioned slots at once (slot 1 is set today; the bitmask
  has room for all). A second, separate per-slot mask at `META+0x64` (`0x20a304`, the
  "record-present" map written by op 0x18) also tracks registration.
- **Controller layer (real keys):** the LTK/IRK bond record is written by the **RTL8762C ROM SMP
  stack into its own FTL region**, invisible to app code. I reconstructed all 91 app `ftl_save`
  (ROM `0xb918`) sites and mapped the app FTL layout (META at offset 4/len 0x60; label table at
  0x2230/0x900; config/IR/screen regions at 0x64,0x674,0x6ac,0xf48,0x1008,0x10c8,0x16ec,…). **None
  is a bond/key store.** So the number of controller bonds is governed by the ROM + `max_le_paired_device`.

## Q3 — Why does `0x81e172` exit without pairing under v5? Every gate it hits.
`0x81e172` has three sequential gates:
- **Gate A `0x81e176`:** `ldrb [0x20dbe5]; cbz → 0x81e18c` (log + return). Fires whenever the BLE
  stack is not up.
- **Gate B `0x81e17e`:** link-state byte `[0x20a808] == 0` → ordinary action (`0x81cb0c`).
- **Gate C `0x81e182`:** `[0x20a808] != 3` → return; `== 3` → queue check `0x81e2f2`, `0x20e010`
  checks, then start-pairing-adv `0x81deac(2)`. `0x20a808 == 3` itself only holds while bondable
  advertising is already active.

**v5 is a provable no-op.** v5 changes `0x81d53c` from `beq 0x81d550` to `b 0x81d54a` (into `0x81e172`).
Any path that reaches `0x81d53c` has already fallen through `0x81d532 (beq 0x81d54a when [0x20dbe5]==1)`,
so reaching `0x81d53c` **guarantees `[0x20dbe5] != 1`** (and the writers only ever store 0 or 1, so it
is 0). v5's new edge therefore always enters `0x81e172` with the stack down → **Gate A returns
immediately, doing nothing.** No LED, no advertising. v1–v3 patch the same neighbourhood and share the
fate. (Side note: the handoff labelled `[0x20b0b0+0x15]` "selected profile type"; it is actually a
STATE byte — 0 idle / 1 advertising / 4 bonded — set to 4 on bond-success at `0x811904` and to 1 by
the dispatcher. The `cmp #4` is a state test, not a type test.)

## Q4 — Does the RTL8762C/app store only one HID peer/bond?
- **Links:** `le_gap_init(1)` at `0x810ce8` (called once) ⇒ **one LE link at a time.** Expected —
  you switch hosts, you don't hold two live connections. Not the bond count.
- **Bonds:** the app makes **no `gap_config_max_le_paired_device(n)` call** (whole boot/init path
  disassembled), so the ceiling is the **ROM default, which is not in this image and I could not read**.
- **Identity:** a single 6-byte "current identity" buffer at `0x2116e4`, zeroed on radio-down
  (`0x81de36`) and rewritten per slot when advertising (`0x81e216`–`0x81e22a`). The per-slot address is
  the factory `<CONFIG_ADDR>` with the last two bytes swapped and slot added: byte4←0xB0,
  byte5←0x79+slot (slot1→…7A, slot4→…7D). **Only one identity is live at any instant** — matching the
  live trace where selecting LAPTOP tears down the …7A link.
- **No proactive bond-clear in the pairing path** (`0x81e172`/`0x81deac` contain no
  `le_bond_clear/delete`). Distinct peers (desktop PC vs laptop PC) are distinct bond records.

**Verdict:** I **cannot** state plainly that dual-host is impossible. The design — per-slot
`0x79+slot` identities that only make sense if the controller keeps one bond per host, no proactive
clear, per-slot provisioned bits — points to the ROM holding ≥2 bonds. The single risk is a ROM
default of 1, which would LRU-evict slot 1 when slot 4 pairs. That number is unreadable statically;
one live test settles it (Q5).

## Q5 — Is a code change viable? Exact plan, DESKTOP-bond-preserving.
**A firmware patch is the wrong tool and is not needed.** The provisioned bit can only be set by a
real bond, and the block on slot 4 is that nothing triggers bondable advertising for it:
- Activation/select `0x81d55a` gates HID bring-up on `is_provisioned(slot)` at `0x81d792`
  (`cbz → 0x81d7e8`). Provisioned → reconnect stored bond; **unprovisioned → generic device switch,
  no radio, no advertising.** That is why selecting LAPTOP only deactivates DESKTOP and Vol+ + OK
  does nothing (radio never came up).
- The real enter-pairing path is a **separate command code**: `[0x20b451] == 0x41` → activation
  island `0x81d91e` → ROM bondable advertising, and it is **NOT gated by the provisioned bit**. But
  event 0x41 is raised by the DEVICES-screen "pair" action / the OFA app, not by any `57`-opcode —
  matching the long-proven field fact that a config-created device never enters BLE pairing while an
  app-created one does.

### Recommended path (no flash, non-destructive)
Provision slot 4 exactly the way slot 1 (DESKTOP) was done: **use the OFA app to create + pair the
LAPTOP device** (proven recipe: app-created type-3 Fire → Vol+ + OK pair → optional config-flip
type→4). A genuine bond completes, `0x811954` sets `META+8` bit 3, and slot 4 becomes a real second
HID host. No firmware change touches the gesture or the mask.

### If a firmware change is still wanted (higher risk, still ROM-cap-bound)
The minimal *correct* patch is not the gesture. It is to make the **unprovisioned branch of activation
`0x81d796`** post enter-pairing (route toward the `0x81d91e` island / event 0x41) instead of the
generic switch, so that selecting an unprovisioned type-4 slot advertises bondably. This is riskier
than the app route and still cannot exceed the ROM bond cap, so it should only follow the live test
below.

### Proof plan that preserves and verifies the DESKTOP bond
1. Record the DESKTOP (…7A) bond and confirm a working keypress **before** anything.
2. Trigger slot-4 pairing by the app route above; pair the laptop; confirm a real …7D HID bond
   (encrypted link, keypresses on the laptop).
3. Switch back to slot 1 and confirm the DESKTOP HID link **resumes encrypted**. If it does, the ROM
   held ≥2 bonds (this is the ONLY way to read the ROM's `max_le_paired_device`). If DESKTOP is gone,
   the ROM cap is 1 and dual-host is not addable without a bond the controller can't hold.

### Hard don'ts (bond-destroying, confirmed in code)
- `W 11 01` (delete slot 1), `W 18 00` / op 0x18 with a zero slot arg (`0x819430`: zeroes both masks
  and erases ALL BLE keys via ROM `0x8139d4(0x0087e000)`), and the factory-wipe `0x816358` each
  destroy slot 1's bond.
- `W 11 04` (delete slot 4) is **safe** — every clear is single-bit `bics 1<<(slot-1)`, and slot 4 is
  unprovisioned so its bond-erase leaf isn't even reached. It only resets slot 4's record fields.

## Corroboration
Cross-checked by three independent passes (this session + two subagents): the W-opcode table
(`0x8187e4`), the mask writer/reader set, the mode dispatcher, the pairing-completion handler, the
identity buffer, and the boot/init `le_gap_init(1)`. Full working notes:
`../../../scratchpad/FINDINGS-laptop-pairing.md` (session scratchpad, not committed).
