"""Amp / TV IR-over-BLE — reuses codec_amp (normal bond, no config chord).

The remote emits Yamaha/Sony IR autonomously when we write a W16 frame over its normal
BLE bond — no ACTIVITIES+DEVICES chord needed (that is only for the *config* link).
This must run on the machine whose Bluetooth is in range of the remote — the laptop
when the author is casting. codec_amp opens/closes its own connection per call.
"""
import codec_amp as amp

# curated groups for the button grid (name, label) — names are codec_amp commands
GROUPS = [
    ("Power / cast", [
        ("cast-on", "Cast ON  (amp on + TV-view)"),
        ("sleep-off", "Sleep OFF  (amp off)"),
        ("power-on", "Amp power on"),
        ("power-off", "Amp power off"),
        ("tv-power", "TV power (Sony)"),
    ]),
    ("Volume", [
        ("vol-up", "Vol +"),
        ("vol-down", "Vol −"),
        ("mute", "Mute"),
    ]),
    ("Modes / DSP", [
        ("tv-view", "TV view ★"),
        ("movie", "Movie"),
        ("music-enh", "Music enh"),
        ("2ch", "2ch"),
        ("5ch", "5ch"),
        ("game", "Game"),
    ]),
    ("Inputs", [
        ("in-dvd", "DVD"),
        ("in-cbl", "CBL/TV"),
        ("in-cd", "CD"),
        ("in-tuner", "Tuner"),
        ("in-vaux", "V-AUX"),
    ]),
]


def describe(command):
    """Human detail for a command name (slot/keyid/shots)."""
    if command in amp.SEQ:
        return " + ".join(f"slot{s}:{k:#04x}" for s, k in amp.SEQ[command])
    if command in amp.CMDS:
        s, k, shots = amp.CMDS[command]
        return f"slot {s} key {k:#04x}" + (f" ×{shots}" if shots > 1 else "")
    return "?"


async def fire(command, count=None, gap=0.4):
    """Resolve and send an IR command. Returns the number of shots sent."""
    seq = amp.resolve(command, count)
    await amp.send(seq, gap)
    return len(seq)
