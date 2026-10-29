#!/usr/bin/env python3
"""OFA URC7985 Control Desk.

A local-first command line front end for the audited URC7985 tools in this
directory.  It never contacts a cloud service.  Every operation gets a fresh,
timestamped journal in ``ofa/artifacts/control-desk/``.

Normal configuration operations require the remote on its case, phone
Bluetooth off, and ACTIVITIES + DEVICES held. Main firmware operations have a
separate OTA workflow. Screen asset preparation is offline; the direct
PC-to-e-paper uploader requires an explicit confirmation. See SCREEN-RECOVERY.md.
"""
from __future__ import annotations

import ofa_identity
import argparse
import asyncio
import json
import os
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent   # repo root
ARTIFACTS = ROOT / "artifacts"
RUNS = ARTIFACTS / "control-desk"
PYTHON = ROOT / "artifacts" / "venv" / "bin" / "python"
ADDRESS = ofa_identity.CONFIG_ADDR
WR = "00001121-d102-11e1-9b23-00025b00a5a5"
# The original firmware helper preserves the historical inferred names.  Live
# validation established these two printed positions are reversed from that
# inference, so the Control Desk uses physical labels throughout.
PHYSICAL_BUTTONS = {
    1: "Pause", 2: "OK", 4: "Record", 6: "Mic", 7: "Stop", 8: "Up",
    9: "Mute", 10: "FastForward", 11: "ChannelDown", 12: "Back",
    13: "Rewind", 14: "Guide", 15: "Right", 16: "ChannelUp",
    18: "VolumeUp", 19: "VolumeDown", 20: "Down", 21: "Home",
    23: "Power", 24: "Left", 25: "Play", 26: "Apps", 27: "Menu", 29: "Input",
}


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_dir(action: str) -> Path:
    path = RUNS / f"{stamp()}-{action}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def invoke(action: str, argv: list[str]) -> int:
    """Run an existing audited helper and retain its full stdout/stderr."""
    folder = run_dir(action)
    command = [str(PYTHON if PYTHON.exists() else sys.executable), *argv]
    started = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(command, cwd=PROJECT, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (folder / "command.txt").write_text(" ".join(command) + "\n")
    (folder / "output.log").write_text(completed.stdout)
    write_json(folder / "run.json", {
        "schema": "ofa-control-desk-1", "action": action, "started": started,
        "finished": datetime.now(timezone.utc).isoformat(), "returncode": completed.returncode,
        "command": command,
    })
    print(completed.stdout, end="")
    print(f"\nJournal: {folder}")
    return completed.returncode


def config_note() -> None:
    print("Configuration mode: remote on its case; phone Bluetooth off; hold ACTIVITIES + DEVICES.")


def inventory_snapshot(address: str) -> dict:
    """Use the read-only W 02 helper and return the raw full reply."""
    sys.path.insert(0, str(ROOT))
    from read_config import read
    return asyncio.run(read(address))


def activity_records(capture: dict) -> list[dict]:
    """Preserve opaque activity records from the verified 512-byte W 02 reply."""
    replies = [bytes.fromhex(item) for item in capture.get("replies", [])]
    blob = b"".join(replies)
    if len(blob) != 512 or blob[:1] != b"\x02":
        raise ValueError("Expected a complete 512-byte W 02 inventory reply")
    start = 1 + 12 * 22
    return [{"index": index + 1, "raw": blob[start + index * 20:start + (index + 1) * 20].hex(" ")}
            for index in range(12)]


def cmd_status(args: argparse.Namespace) -> int:
    config_note()
    folder = run_dir("status")
    return invoke_existing_in(folder, "touch_pages.py", ["inspect", "--address", args.address,
                              "--output", str(folder / "snapshot.json")])


def invoke_existing_in(folder: Path, script: str, arguments: list[str]) -> int:
    command = [str(PYTHON if PYTHON.exists() else sys.executable), str(ROOT / script), *arguments]
    started = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(command, cwd=PROJECT, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (folder / "command.txt").write_text(" ".join(command) + "\n")
    (folder / "output.log").write_text(completed.stdout)
    write_json(folder / "run.json", {"schema": "ofa-control-desk-1", "action": script,
              "started": started, "finished": datetime.now(timezone.utc).isoformat(),
              "returncode": completed.returncode, "command": command})
    print(completed.stdout, end="")
    print(f"\nJournal: {folder}")
    return completed.returncode


def cmd_labels(args: argparse.Namespace) -> int:
    config_note()
    folder = run_dir("label-set")
    backup = folder / "label-backup.json"
    return invoke_existing_in(folder, "labels.py", ["set", "--address", args.address,
        "--slot", str(args.slot), "--key", args.key, "--text", args.text,
        "--backup", str(backup), "--apply"])


def cmd_pages(args: argparse.Namespace) -> int:
    config_note()
    folder = run_dir(f"pages-{args.page_action}")
    if args.page_action == "inspect":
        parameters = ["inspect", "--address", args.address, "--output", str(folder / "snapshot.json")]
    elif args.page_action in ("enable", "disable"):
        parameters = [args.page_action, "--address", args.address, "--slot", str(args.slot),
                      "--pages", args.pages, "--backup", str(folder / "page-backup.json")]
        if args.page_action == "enable":
            parameters.extend(["--from-slot", str(args.from_slot)])
        if not args.dry_run:
            # touch_pages has no separate apply flag; its default is a backed-up write.
            pass
        else:
            parameters.append("--dry-run")
    else:
        parameters = ["restore", "--address", args.address, "--backup", str(args.backup),
                      "--save-backup", str(folder / "restore-journal.json")]
        if args.dry_run:
            parameters.append("--dry-run")
    return invoke_existing_in(folder, "touch_pages.py", parameters)


def cmd_devices_list(args: argparse.Namespace) -> int:
    config_note()
    folder = run_dir("devices-list")
    return invoke_existing_in(folder, "read_config.py", ["--address", args.address,
                              "--output", str(folder / "devices.json")])


async def rename_remote(address: str, slot: int, name: str, journal: dict, save) -> None:
    from bleak import BleakClient
    from read_config import read
    before = await read(address)
    matches = [record for record in before["records"] if record["slot"] == slot]
    if len(matches) != 1:
        raise ValueError("Selected slot is missing or duplicated; refusing write")
    old = matches[0]
    raw = bytes.fromhex(old["raw"])
    if len(raw) != 22 or raw[0] & 15 != slot:
        raise ValueError("Unexpected device record encoding")
    command = b"W\x14" + raw[:6] + name.encode("ascii").ljust(16, b"\0")
    journal.update(before=before, selected_before=old, command=command.hex(" "), status="write_pending")
    save()
    async with BleakClient(address, timeout=25) as client:
        await client.write_gatt_char(WR, command, response=False)
        await asyncio.sleep(1)
    after = await read(address)
    changed = [record for record in after["records"] if record["slot"] == slot]
    if len(changed) != 1 or changed[0]["name"] != name or changed[0]["raw"][:12] != old["raw"][:12]:
        raise ValueError("Device-name readback did not preserve the record type/code/profile")
    if [r for r in after["records"] if r["slot"] != slot] != [r for r in before["records"] if r["slot"] != slot]:
        raise ValueError("A different device record changed; inspect journal before proceeding")
    journal.update(after=after, selected_after=changed[0], status="verified")
    save()


def cmd_devices_rename(args: argparse.Namespace) -> int:
    if not 1 <= args.slot <= 12:
        raise ValueError("Slot must be 1..12")
    if not args.name.isascii() or not 1 <= len(args.name) <= 15 or "\0" in args.name:
        raise ValueError("Name must be 1..15 printable ASCII characters")
    config_note()
    folder = run_dir("device-rename")
    journal_path = folder / "rename.json"
    journal = {"schema": "ofa-device-rename-1", "address": args.address, "slot": args.slot,
               "requested_name": args.name, "status": "starting"}
    write_json(journal_path, journal)
    def save(): write_json(journal_path, journal)
    try:
        asyncio.run(rename_remote(args.address, args.slot, args.name, journal, save))
    except Exception as error:
        journal["error"] = str(error); save(); raise
    print(f"Renamed slot {args.slot} to {args.name!r}; readback verified. Journal: {folder}")
    return 0


def cmd_devices_dedupe(args: argparse.Namespace) -> int:
    config_note()
    folder = run_dir("devices-dedupe")
    command = ["--address", args.address, "--output", str(folder / "dedupe.json")]
    if args.apply:
        command.append("--apply")
    return invoke_existing_in(folder, "device_order.py", command)


def cmd_activities(args: argparse.Namespace) -> int:
    config_note()
    folder = run_dir("activities-backup")
    try:
        capture = inventory_snapshot(args.address)
        report = {"schema": "ofa-activity-backup-1", "captured": datetime.now(timezone.utc).isoformat(),
                  "address": args.address, "devices": capture["records"],
                  "activities": activity_records(capture),
                  "write_support": False,
                  "note": "Activity records are backed up as opaque 20-byte values. No validated PC-side activity writer exists; preserving them is deliberate."}
        write_json(folder / "activities.json", report)
        print(json.dumps(report, indent=2))
        print(f"\nBackup: {folder / 'activities.json'}")
        return 0
    except Exception as error:
        write_json(folder / "error.json", {"error": str(error)})
        raise


def cmd_keys_inspect(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    from firmware import button_map, verify_image
    data = args.image.read_bytes(); info = verify_image(data)
    entries = button_map(data)
    for entry in entries:
        entry["button"] = PHYSICAL_BUTTONS[entry["matrix_index"]]
    print(json.dumps({"image": info, "type4_buttons": entries}, indent=2))
    return 0


def cmd_keys_patch(args: argparse.Namespace) -> int:
    """Build an isolated type-4 key remap, always offline and journalled."""
    sys.path.insert(0, str(ROOT))
    from firmware import POOL_BASE, ROWS, SHA_OFFSET, button_map, image_digest, verify_image
    if args.button not in PHYSICAL_BUTTONS.values():
        raise ValueError("Use a button listed by keys inspect")
    if not 0 <= args.usage <= 0xFFFF:
        raise ValueError("HID usage must be 0..65535")
    source = bytearray(args.image.read_bytes()); source_info = verify_image(source)
    matrix = next(index for index, name in PHYSICAL_BUTTONS.items() if name == args.button)
    occupied = {source[row + index] for row in ROWS.values() for index in range(30)}
    free = next((index for index in range(0x21, 128) if index not in occupied), None)
    if free is None:
        raise ValueError("No isolated consumer-page pool entry is available")
    output = bytearray(source)
    output[ROWS[4] + matrix] = free
    struct.pack_into("<H", output, POOL_BASE + free * 2, args.usage)
    output[SHA_OFFSET:SHA_OFFSET + 32] = image_digest(output)
    candidate_info = verify_image(output)
    changed = [{"offset": hex(i), "before": f"{a:02x}", "after": f"{b:02x}"}
               for i, (a, b) in enumerate(zip(source, output)) if a != b]
    allowed = {ROWS[4] + matrix, POOL_BASE + 2 * free, POOL_BASE + 2 * free + 1,
               *range(SHA_OFFSET, SHA_OFFSET + 32)}
    if any(int(item["offset"], 16) not in allowed for item in changed):
        raise AssertionError("Unexpected candidate scope")
    folder = run_dir("keymap-patch")
    target = folder / "candidate.bin"; target.write_bytes(output)
    before_map, after_map = button_map(source), button_map(output)
    for entry in before_map + after_map:
        entry["button"] = PHYSICAL_BUTTONS[entry["matrix_index"]]
    manifest = {"schema": "ofa-keymap-patch-1", "status": "OFFLINE CANDIDATE — NOT FLASHED",
                "source": source_info, "candidate": candidate_info,
                "scope": "type-4 only; DESKTOP and LAPTOP share this mapping",
                "button": args.button, "matrix_index": matrix, "new_pool_index": free,
                "new_hid_usage": hex(args.usage), "changes": changed,
                "before_map": before_map, "after_map": after_map,
                "next_step": "Review the manifest and use main-fw plan. Flash execution requires a separate explicit acknowledgement."}
    write_json(folder / "manifest.json", manifest)
    print(f"Built offline candidate: {target}\nManifest: {folder / 'manifest.json'}")
    return 0


def cmd_keys_capture(args: argparse.Namespace) -> int:
    folder = run_dir("key-capture")
    print("This opens only OFA Pro Touch input devices and normally needs sudo. Ctrl-C ends capture.")
    return invoke_existing_in(folder, "log_keys.py", ["--output", str(folder / "keys.jsonl")])


def cmd_debug(args: argparse.Namespace) -> int:
    """Read-only equivalents of the useful app debug views."""
    folder = run_dir(f"debug-{args.debug_action}")
    if args.debug_action == "identity":
        return invoke_existing_in(folder, "read_device.py", ["--address", args.address,
                                  "--output", str(folder / "identity.json")])
    return invoke_existing_in(folder, "scan_remote.py", ["--seconds", str(args.seconds),
                              "--output", str(folder / "advertisements.jsonl")])


def cmd_main_fw(args: argparse.Namespace) -> int:
    folder = run_dir("main-fw-plan" if not args.execute else "main-fw-execute")
    log = folder / "transfer.json"
    command = [str(args.image), "--stock", str(args.stock), "--apk", str(args.apk), "--log", str(log)]
    if args.execute:
        if args.confirm != "FLASH MAIN FIRMWARE":
            raise ValueError("Execution requires --confirm 'FLASH MAIN FIRMWARE'")
        command.append("--execute")
    return invoke_existing_in(folder, "flash.py", command)


def cmd_screen(args: argparse.Namespace) -> int:
    if args.screen_action == "stage":
        return cmd_screen_stage(args)
    if args.screen_action == "icon":
        folder = run_dir("screen-icon-patch")
        command = ["patch", str(args.base), args.target, str(args.png),
                   "--resample", args.resample, "--output-dir", str(folder / "candidate")]
        return invoke_existing_in(folder, "screen_images.py", command)
    if args.screen_action == "direct":
        folder = run_dir("screen-direct-plan" if args.direct_action == "plan" else "screen-direct-execute")
        command = [args.direct_action, str(args.image)]
        if args.direct_action == "execute":
            if args.confirm != "FLASH E-PAPER DIRECT":
                raise ValueError("Direct upload requires --confirm 'FLASH E-PAPER DIRECT'")
            command.extend(["--address", args.address, "--timeout", str(args.timeout),
                            "--log", str(folder / "transfer.json"), "--confirm", args.confirm])
        return invoke_existing_in(folder, "screen_direct.py", command)
    if args.screen_action == "write":
        raise ValueError("Use screen direct plan first, then screen direct execute with its exact confirmation. See SCREEN-RECOVERY.md.")
    folder = run_dir("screen-audit")
    command = [str(args.main_stock), str(args.screen_image), "--output", str(folder / "recovery-audit.json")]
    if args.capture:
        command.extend(["--capture", str(args.capture)])
    return invoke_existing_in(folder, "check_screen_recovery.py", command)


def cmd_screen_stage(args: argparse.Namespace) -> int:
    """Stage a validated screen image for the proven Android app uploader.

    This does not open a Bluetooth connection or press the app's upgrade
    button. It makes the image available in Downloads, launches the requested
    OFA package, and journals each ADB command and the offline packet plan.
    """
    if not args.image.is_file():
        raise FileNotFoundError(args.image)
    folder = run_dir("screen-app-stage")
    image = args.image.resolve()
    # Reproduce the existing app packet stream first; this is fully offline.
    dry = subprocess.run([str(PYTHON if PYTHON.exists() else sys.executable),
                          str(ROOT / "screen_app_ota_dryrun.py"), str(image)],
                         cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (folder / "packet-plan.log").write_text(dry.stdout)
    if dry.returncode:
        write_json(folder / "run.json", {"status": "offline_validation_failed", "image": str(image)})
        print(dry.stdout, end="")
        return dry.returncode
    plan = json.loads(dry.stdout)
    destination = f"{args.destination.rstrip('/')}/{image.name}"
    adb = [args.adb]
    if args.serial:
        adb.extend(["-s", args.serial])
    commands = [adb + ["start-server"], adb + ["shell", "mkdir", "-p", args.destination],
                adb + ["push", str(image), destination],
                adb + ["shell", "monkey", "-p", args.package,
                       "-c", "android.intent.category.LAUNCHER", "1"]]
    events = []
    for command in commands:
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        events.append({"command": command, "returncode": completed.returncode, "output": completed.stdout})
        if completed.returncode:
            write_json(folder / "stage.json", {"schema": "ofa-screen-app-stage-1", "image": str(image),
                "destination": destination, "packet_plan": plan, "events": events, "status": "adb_failed"})
            print(completed.stdout, end="", file=sys.stderr)
            print(f"Journal: {folder}", file=sys.stderr)
            return completed.returncode
    report = {"schema": "ofa-screen-app-stage-1", "image": str(image), "destination": destination,
              "package": args.package, "packet_plan": plan, "events": events,
              "status": "staged; no Bluetooth update was initiated by the CLI",
              "next_step": "In the open OFA app, use E-paper Select File, choose the staged .bin, verify its name, then tap Start Upgrade."}
    write_json(folder / "stage.json", report)
    print(f"Staged: {destination}\nOpened: {args.package}\nNo update has started. Choose the file and tap Start Upgrade in the app.\nJournal: {folder}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    candidates = {
        "venv_python": PYTHON, "main_stock": ARTIFACTS / "ble24" / "stock-0.5.7.bin",
        "installed_main_candidate": ARTIFACTS / "pause-playpause-v1" / "candidate-0.5.7-pause-playpause.bin",
        "screen_handoff": PROJECT / "docs" / "notes" / "SCREEN-RECOVERY.md",
        "final_handoff": PROJECT / "docs" / "handoffs" / "CLAUDE-FINAL-HANDOFF-2026-09-22.md",
    }
    report = {name: {"path": str(path), "exists": path.exists()} for name, path in candidates.items()}
    report["bleak_available"] = shutil.which("bluetoothctl") is not None
    print(json.dumps(report, indent=2))
    return 0 if all(value["exists"] for key, value in report.items() if isinstance(value, dict)) else 1


def interactive() -> int:
    print("OFA URC7985 Control Desk\n1) Health check\n2) Read remote status\n3) Show command help")
    choice = input("Choose: ").strip()
    if choice == "1": return cmd_doctor(argparse.Namespace())
    if choice == "2": return cmd_status(argparse.Namespace(address=ADDRESS))
    print("Run: python ofa/ofa_control.py --help")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = p.add_subparsers(dest="command")
    doctor = subs.add_parser("doctor", help="check the local installation"); doctor.set_defaults(func=cmd_doctor)
    status = subs.add_parser("status", help="read inventory and touchscreen pages"); status.add_argument("--address", default=ADDRESS); status.set_defaults(func=cmd_status)
    labels = subs.add_parser("label", help="set a custom touchscreen label (readback verified)")
    labels.add_argument("--address", default=ADDRESS); labels.add_argument("--slot", type=int, required=True)
    # red/green/yellow/blue/return/info plus spare1..spare10 (spare-keys firmware).
    sys.path.insert(0, str(ROOT))
    from custom_firmware import ALIASES
    labels.add_argument("--key", choices=tuple(ALIASES), required=True)
    labels.add_argument("--text", required=True); labels.set_defaults(func=cmd_labels)
    pages = subs.add_parser("pages", help="inspect, copy, remove, or restore touchscreen pages")
    ps = pages.add_subparsers(dest="page_action", required=True)
    pi = ps.add_parser("inspect"); pi.add_argument("--address", default=ADDRESS); pi.set_defaults(func=cmd_pages)
    for action in ("enable", "disable"):
        sp = ps.add_parser(action); sp.add_argument("--address", default=ADDRESS); sp.add_argument("--slot", type=int, required=True)
        sp.add_argument("--pages", default="all"); sp.add_argument("--dry-run", action="store_true")
        if action == "enable": sp.add_argument("--from-slot", type=int, required=True)
        sp.set_defaults(func=cmd_pages)
    pr = ps.add_parser("restore"); pr.add_argument("--address", default=ADDRESS); pr.add_argument("--backup", type=Path, required=True); pr.add_argument("--dry-run", action="store_true"); pr.set_defaults(func=cmd_pages)
    devices = subs.add_parser("devices", help="list, safely rename, or remove duplicate menu positions")
    ds = devices.add_subparsers(dest="device_action", required=True)
    dl = ds.add_parser("list"); dl.add_argument("--address", default=ADDRESS); dl.set_defaults(func=cmd_devices_list)
    dr = ds.add_parser("rename"); dr.add_argument("--address", default=ADDRESS); dr.add_argument("--slot", type=int, required=True); dr.add_argument("--name", required=True); dr.set_defaults(func=cmd_devices_rename)
    dd = ds.add_parser("dedupe"); dd.add_argument("--address", default=ADDRESS); dd.add_argument("--apply", action="store_true"); dd.set_defaults(func=cmd_devices_dedupe)
    activities = subs.add_parser("activities", help="back up opaque activity records (writer not yet established)"); activities.add_argument("--address", default=ADDRESS); activities.set_defaults(func=cmd_activities)
    keys = subs.add_parser("keys", help="inspect, capture, or build an offline type-4 HID remap")
    ks = keys.add_subparsers(dest="key_action", required=True)
    ki = ks.add_parser("inspect"); ki.add_argument("image", type=Path); ki.set_defaults(func=cmd_keys_inspect)
    kp = ks.add_parser("patch"); kp.add_argument("image", type=Path); kp.add_argument("--button", required=True); kp.add_argument("--usage", type=lambda x: int(x, 0), required=True); kp.set_defaults(func=cmd_keys_patch)
    kc = ks.add_parser("capture"); kc.set_defaults(func=cmd_keys_capture)
    debug = subs.add_parser("debug", help="read-only remote identity, GATT capability, and advertisement captures")
    db = debug.add_subparsers(dest="debug_action", required=True)
    di = db.add_parser("identity", help="capture normal-mode GATT identity and OTA capabilities")
    di.add_argument("--address", default=ADDRESS); di.set_defaults(func=cmd_debug)
    ds = db.add_parser("scan", help="capture only advertisements that belong to this remote")
    ds.add_argument("--seconds", type=float, default=20); ds.set_defaults(func=cmd_debug)
    firmware = subs.add_parser("main-fw", help="offline-plan or explicitly execute a main-app update")
    firmware.add_argument("image", type=Path); firmware.add_argument("--stock", type=Path, required=True); firmware.add_argument("--apk", type=Path, required=True)
    firmware.add_argument("--execute", action="store_true"); firmware.add_argument("--confirm", default=""); firmware.set_defaults(func=cmd_main_fw)
    screen = subs.add_parser("screen", help="e-paper audits, offline icon candidates, and explicitly confirmed direct uploads")
    ss = screen.add_subparsers(dest="screen_action", required=True)
    sa = ss.add_parser("audit"); sa.add_argument("main_stock", type=Path); sa.add_argument("screen_image", type=Path); sa.add_argument("--capture", type=Path); sa.set_defaults(func=cmd_screen)
    st = ss.add_parser("stage", help="validate, copy to Android, and open the proven OFA app upload path")
    st.add_argument("image", type=Path); st.add_argument("--adb", default="adb"); st.add_argument("--serial")
    st.add_argument("--package", default="com.uei.ofa.advanced")
    st.add_argument("--destination", default="/sdcard/Download/OFA")
    st.set_defaults(func=cmd_screen)
    icon = ss.add_parser("icon", help="offline: replace one known shortcut-logo bitmap from a PNG")
    icon.add_argument("base", type=Path, help="v013f stock image or a font-only derivative")
    icon.add_argument("target", choices=("netflix", "prime", "youtube", "hbo", "rakuten", "disney", "paramount"))
    icon.add_argument("png", type=Path); icon.add_argument("--resample", choices=("nearest", "lanczos"), default="nearest")
    icon.set_defaults(func=cmd_screen)
    direct = ss.add_parser("direct", help="direct PC e-paper plan or explicitly confirmed uploader")
    direct.add_argument("direct_action", choices=("plan", "execute")); direct.add_argument("image", type=Path)
    direct.add_argument("--address", default=ADDRESS); direct.add_argument("--timeout", type=float, default=12)
    direct.add_argument("--confirm", default=""); direct.set_defaults(func=cmd_screen)
    sw = ss.add_parser("write", help="explains the proven app-assisted recovery path"); sw.set_defaults(func=cmd_screen)
    return p


def main() -> int:
    p = parser(); args = p.parse_args()
    if not args.command: return interactive()
    try: return args.func(args)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
