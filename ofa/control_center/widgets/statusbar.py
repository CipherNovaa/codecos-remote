"""Persistent link-status strip for the window's QStatusBar."""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel


class LinkStatusWidget(QWidget):
    """Compact permanent widget: link state, main fw, screen fw, battery, BLE busy."""
    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(12)
        self.link = QLabel("link ✗")
        self.link.setObjectName("dim")
        self.fw = QLabel("")
        self.fw.setObjectName("dim")
        self.batt = QLabel("")
        self.batt.setObjectName("dim")
        self.busy = QLabel("")
        self.busy.setObjectName("warn")
        for w in (self.busy, self.batt, self.fw, self.link):
            lay.addWidget(w)

    def set_link(self, info):
        """info: dict from engine.link.probe, or None for down."""
        if not info:
            self.link.setText("link ✗")
            self.link.setStyleSheet("color:#f38ba8;")
            self.fw.setText("")
            self.batt.setText("")
            return
        self.link.setText("link ✓")
        self.link.setStyleSheet("color:#a6e3a1;")
        sv = info.get("screen_version")
        self.fw.setText(f"main {info.get('main_version', '?')} · "
                        f"screen {('v' + str(sv)) if sv is not None else 'v?'}")
        b = info.get("battery_pct")
        self.batt.setText(f"batt {b}%" if b is not None else "")

    def set_busy(self, text):
        self.busy.setText(text or "")
