"""The shared BLE config-link: session context manager + a fast preflight probe.

All in-process BLE engines (screensaver, lights) open a connection the same way the
CLI tools do — one BleakClient per logical action, bound to the qasync loop. The
``probe`` is deliberately cheap (~4 register reads / ~1.5 s, NOT status()'s ~100
reads) so the Connect panel and the post-write status refresh stay snappy.

On timeout we raise ``LinkDown`` carrying the ritual text, so any panel can show the
"phone BT off / AMPLIFIER / hold ACTIVITIES+DEVICES" checklist without special-casing.
"""
import asyncio

import codec_screensaver as ss

from .. import constants as K


class LinkDown(Exception):
    """The config link is not up. Message carries the one-line ritual reminder."""
    def __init__(self, detail=""):
        self.detail = detail
        super().__init__(K.RITUAL_ONE_LINER + (f"  ({detail})" if detail else ""))


class LinkBusy(Exception):
    """The single device lock is already held; the caller should try again later."""
    def __init__(self):
        super().__init__("device busy")


class BleSession:
    """async context manager: connect + start_notify + a ready codec_screensaver.Link.

        async with BleSession() as s:
            ver = await s.link.identify()
            batt = await s.link.reg(17)

    Exposes ``.client`` and ``.queue`` too, for engines (codec_led) that take them raw.
    """
    def __init__(self, timeout=25):
        self.timeout = timeout
        self.client = None
        self.queue = None
        self.link = None

    async def __aenter__(self):
        from bleak import BleakClient
        self.client = BleakClient(ss.ADDR, timeout=self.timeout)
        try:
            await self.client.__aenter__()
        except Exception as e:                       # never connected → link is down
            raise LinkDown(str(e))
        # If setup fails AFTER connecting, disconnect before propagating so we don't
        # leak a live link (the remote allows only one) that blocks every later op.
        try:
            self.queue = asyncio.Queue()
            await self.client.start_notify(ss.NOTIFY_UUID,
                                           lambda _c, d: self.queue.put_nowait(bytes(d)))
            mtu = getattr(self.client, "mtu_size", 23) or 23
            chunk = max(16, min(ss.MAX_DATA, mtu - 3 - 5))
            self.link = ss.Link(self.client, self.queue, chunk)
        except Exception as e:
            try:
                await self.client.__aexit__(type(e), e, None)
            except Exception:
                pass
            raise LinkDown(str(e))
        return self

    async def __aexit__(self, *exc):
        try:
            await self.client.__aexit__(*exc)
        except Exception:
            pass
        return False


async def probe():
    """Fast link health check. Returns a dict; raises LinkDown only if the link is down.

    identify() (a main-MCU round-trip) IS the proof the config link is up. The extras —
    screen firmware version, battery, slot map — go through the screen-MCU relay and can
    flake (screen asleep/slow) WITHOUT meaning the link is down, so each is best-effort:
    a flake leaves that field None but still reports the link as UP.

    dict keys: main_version, screen_version|None, battery_pct|None, slot_map|None,
    slots (list[bool]), slot_map_str.
    """
    try:
        async with BleSession() as s:
            main = await s.link.identify()               # 'SS01'/'SS02' or raises → link down
            info = {"main_version": main, "screen_version": None, "battery_pct": None,
                    "slot_map": None, "slots": [], "slot_map_str": "?"}
            try:
                info["screen_version"] = await s.link.reg(16)
            except Exception:
                pass
            try:
                info["battery_pct"] = await s.link.reg(17)
            except Exception:
                pass
            try:
                smap = await s.link.reg(0)
                info["slot_map"] = smap
                info["slots"] = [bool(smap >> i & 1) for i in range(ss.NSLOTS)]
                info["slot_map_str"] = ss.slot_map_str(smap)
            except Exception:
                pass
            try:
                await s.link.restore_version()            # leave the version cache clean
            except Exception:
                pass
            return info
    except LinkDown:
        raise
    except Exception as e:                               # incl. BleakError → treat as down
        raise LinkDown(str(e))
