"""Panel base class — shared services proxied to the MainWindow."""
from PyQt6.QtWidgets import QWidget


class Panel(QWidget):
    title = "Panel"
    #: True if this panel does BLE config-link work (drives the ritual card / status).
    needs_link = False

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.build()

    # subclasses override
    def build(self):
        pass

    def on_link(self, info):
        """Called with the latest link probe dict (or None when down)."""
        pass

    # ── shared services ──
    def status(self, msg, ms=5000):
        self.win.status.showMessage(msg, ms)

    def run_async(self, coro, on_done=None, on_error=None):
        return self.win.run_async(coro, on_done=on_done, on_error=on_error)

    def run_ble(self, make_coro, on_done=None, on_error=None, busy="working…"):
        return self.win.run_ble(make_coro, on_done=on_done, on_error=on_error, busy=busy)
