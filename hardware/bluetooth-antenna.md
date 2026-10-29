# Desktop Bluetooth antenna: a quarter-wave from a piece of coax

<!-- MEDIA: photo of the antenna on the rear I/O: the ~31 mm vertical wire standing out of the RP-SMA jack, with the hand-wound coil at its base visible. A second close-up of the coil gripping the centre pin is a bonus. -->

## The problem

The desktop's Bluetooth is the motherboard's Intel Wireless-AC 9560 (CNVi), wired to
two RP-SMA jacks on the rear I/O. The board was bought second-hand without its
antenna, so the jacks were bare. The remote only stayed connected when it was sitting
on the case: about **-91 to -100 dBm** next to the PC, with Connection Timeout drops.

A first fix, a bare wire taped onto one jack's centre pin, got it working at the desk
(61/61 presses delivered) but gave about **-67 dBm point-blank and around -95 dBm
across the room**, with link drops.

## The fix (no purchase)

A 2.4 GHz quarter wave is about **31 mm**. The antenna is:

- ~31 mm of centre conductor stripped out of a scrap of coax,
- a small coil hand-wound at its base that **grips the RP-SMA centre pin** (no solder,
  so the jack stays usable for a real antenna later),
- standing **vertical, out in open air**. Lying against the case metal caps it at
  about -67 dBm again.

## Measured

With `tools/radio/ofa_range.py`, which logs every keypress together with the link RSSI
at that instant:

| Position | Before (wire) | After (λ/4) |
|---|---|---|
| Point-blank, on the case | ~-67 dBm | -25 to -43 dBm |
| Across the room, remote aimed | ~-95 dBm, 2 drops | ~-74 dBm, 0 drops |
| Across the room, remote lying unaimed | n/a | ~-78 dBm |

About **20 dB better** at range, and aiming the remote only buys about 4 dB.

## Measuring RSSI on Linux: the traps

- **RSSI needs root.** As a normal user the BlueZ mgmt socket returns nothing
  (`btmgmt con` errors with "Too small (0 bytes)"), and `hcitool` is gone from current
  distros. Both tools talk to the mgmt socket directly (`Get Connection Information`,
  opcode `0x0031`) and must run with `sudo`.
- **The remote connects per keypress and drops right after**, as a new `uhid` input
  device each time. So `ofa_range.py` polls RSSI on each key event and re-scans
  `/sys/class/input/event*/device/name` for "OFA Pro Touch" on every reconnect.
  `rssi_logger.py` instead polls fast and asks you to hold a key down.
- **The first reading after a connect is `127`**, the controller's "not ready" value.
  Ignore it.
- `ofa_range.py` **grabs the remote exclusively** (`EVIOCGRAB`) so a stray Power press
  can't reach the desktop while you walk around testing. The remote won't drive the
  PC while it runs.

## Related: batteries

The remote takes 4×AAA. With NiMH rechargeables (1.2 V) its alkaline-calibrated gauge
lies: it read 49 % on a pack that was really near 14 %, and the sag at the end of a
charge shows up as **repeated inputs**. Use fresh alkalines for any firmware flash;
`flash.py` refuses to start below 50 % anyway.
