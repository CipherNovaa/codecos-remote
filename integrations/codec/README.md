# codec integration

The remote was customised to drive **codec**, the author's own Qt/libmpv media player
and cast hub. Almost everything in this repo is codec-agnostic (it speaks HID, so any
app can bind the keys), but a few pieces assume the author's household. They live here.

## `codec_amp.py` (+ the `codec_amp` wrapper): amplifier IR over Bluetooth

**The problem.** The TV sends audio to a 2009 AV receiver over optical. Optical is a
fixed digital pass-through with no back channel, and the receiver's HDMI is 1.3, so
there is no ARC/CEC either. Checked on the TV with `adb shell dumpsys audio` and
`dumpsys hdmi_control`: the TV's volume keys can never reach this amp.

**The fix.** The remote already knows the amp's IR codes (it is a universal remote).
The config channel has an opcode that makes the remote *press a key on a device slot*:

```
57 16 <slot> <key id> 00 00 00     -> write-no-response to config char 0x1121
```

So a PC within Bluetooth range can make the remote emit the amp's IR on command. No
IR blaster, no new amp, no extra hardware. It works over a normal bonded connection,
without the ACTIVITIES+DEVICES hold.

`codec_amp.py` maps names to `(slot, key id)` pairs: discrete power on/off, volume,
mute, the receiver's DSP modes and inputs, and TV power. Some modes need a fast
double press, which it sends in one connection.

```sh
integrations/codec/codec_amp --list
integrations/codec/codec_amp cast-on        # amp on + switch to the TV-viewing mode
integrations/codec/codec_amp vol-up 3
```

**Adapting it:** the slot numbers (`AMP = 2`, `TV = 3`) and key ids are specific to the
author's remote setup and receiver. Read your own with `ofa/ofa_read.py` (it lists the
key ids on each device slot) and edit the tables. The key ids come from the remote's
own IR code set, not from this project.

The Control Center's **IR & Amp** tab is a button grid over this module; it finds it
through `ofa/control_center/__init__.py`, which adds this folder to the import path.

## What codec does with the keys (not in this repo)

The codec side lives in codec's own source: a scancode-keyed handler in its controller
binds the physical keys, the six named touchscreen keys (F14-F19) and the ten spare
keys, with a 700 ms tap-vs-hold threshold that ignores host auto-repeat. That code
belongs to the media player and is not published here.
