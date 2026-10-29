"""MainWindow — tabs, status bar, pywal theme, and the shared async runners.

Cross-cutting execution:
  • run_async(coro)      — fire-and-forget a coroutine on the qasync loop, errors → status
  • run_ble(make_coro)   — same, but serialised behind a single-link busy guard and with
                            a LinkDown → ritual-card fallback (only one BLE op at a time)
The Qt event loop *is* the asyncio loop (qasync), so engine progress callbacks run on the
GUI thread and may touch widgets directly.
"""
import asyncio

from PyQt6.QtCore import QFileSystemWatcher
from PyQt6.QtWidgets import QMainWindow, QTabWidget, QStatusBar

from . import constants as K
from .theme import load_wal_colors, build_stylesheet, WAL_COLORS
from .widgets.statusbar import LinkStatusWidget
from .engine.link import LinkDown, LinkBusy, probe
from .panels import PANELS


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OFA Remote Control Center")
        self.setMinimumSize(1040, 720)
        self.resize(1200, 800)

        self._device_busy = False       # one lock for ALL device access (BLE + BLE subprocess)
        self._flashing = False
        self.link_cache = None

        # status bar + permanent link strip
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.link_widget = LinkStatusWidget()
        self.status.addPermanentWidget(self.link_widget)
        self.status.showMessage("Ready — check the link on the Connect tab")

        # tabs
        self.tabs = QTabWidget()
        self.panels = []
        for cls in PANELS:
            p = cls(self)
            self.panels.append(p)
            self.tabs.addTab(p, p.title)
        self.setCentralWidget(self.tabs)

        # pywal theme + live watcher
        self._apply_theme()
        self._watcher = QFileSystemWatcher()
        if WAL_COLORS.exists():
            self._watcher.addPath(str(WAL_COLORS))
        self._watcher.fileChanged.connect(self._on_theme_file_changed)

    # ── theming ──
    def _apply_theme(self):
        self.setStyleSheet(build_stylesheet(load_wal_colors()))

    def _on_theme_file_changed(self, _path):
        # pywal rewrites colors.json via atomic rename, which drops the watch — re-add it.
        self._apply_theme()
        if WAL_COLORS.exists() and str(WAL_COLORS) not in self._watcher.files():
            self._watcher.addPath(str(WAL_COLORS))

    # ── one device lock for ALL remote access (in-process BLE + BLE subprocesses) ──
    def acquire_device(self, busy="working…"):
        """Take the single device lock. Returns False (and warns) if already held."""
        if self._device_busy:
            self.status.showMessage("⏳ device busy — wait for the current operation", 4000)
            return False
        self._device_busy = True
        self.link_widget.set_busy(busy)
        return True

    def release_device(self):
        self._device_busy = False
        self.link_widget.set_busy("")

    def set_flashing(self, on):
        """While a no-rollback firmware flash runs, lock the device AND block every other
        tab so a stray BLE op cannot disrupt the DFU stream (brick risk)."""
        self._flashing = on
        fw_idx = next((i for i, p in enumerate(self.panels)
                       if p.__class__.__name__ == "FirmwarePanel"), None)
        for i in range(self.tabs.count()):
            self.tabs.setTabEnabled(i, (not on) or i == fw_idx)

    # ── async runners ──
    def run_async(self, coro, on_done=None, on_error=None):
        async def _wrap():
            try:
                r = await coro
                if on_done:
                    on_done(r)
            except Exception as e:                     # noqa: BLE001
                (on_error or self._default_error)(e)
        return asyncio.ensure_future(_wrap())

    def run_ble(self, make_coro, on_done=None, on_error=None, busy="working…"):
        """Serialise BLE work behind the device lock. make_coro is a coroutine factory.

        If the lock is already held, invoke on_error with a LinkBusy so callers that
        pre-disabled their controls get a chance to restore them (audit M2)."""
        if not self.acquire_device(busy):
            (on_error or self._default_error)(LinkBusy())
            return None

        async def _wrap():
            try:
                r = await make_coro()
                if on_done:
                    on_done(r)
            except Exception as e:                     # noqa: BLE001
                (on_error or self._default_error)(e)
            finally:
                self.release_device()
        return asyncio.ensure_future(_wrap())

    def _default_error(self, exc):
        if isinstance(exc, LinkBusy):
            self.status.showMessage("⏳ device busy — try again in a moment", 4000)
            return
        if isinstance(exc, LinkDown):
            self.note_link_down(exc.detail)
        else:
            self.status.showMessage(f"⚠ {exc}", 9000)

    # ── link state broadcast ──
    def refresh_link(self):
        """Cheap preflight probe → update the status strip and notify all panels."""
        def ok(info):
            self.link_cache = info
            self.link_widget.set_link(info)
            sv = info.get("screen_version")
            b = info.get("battery_pct")
            self.status.showMessage(
                f"link ✓  main {info['main_version']} · "
                f"screen {('v' + str(sv)) if sv is not None else 'v?'} · "
                f"batt {str(b) + '%' if b is not None else '?'} · {info['slot_map_str']}", 8000)
            for p in self.panels:
                p.on_link(info)
        self.run_ble(probe, on_done=ok, on_error=self._default_error, busy="probing…")

    def note_link_down(self, detail=""):
        self.link_cache = None
        self.link_widget.set_link(None)
        self.status.showMessage("✗ " + K.RITUAL_ONE_LINER, 12000)
        for p in self.panels:
            p.on_link(None)
