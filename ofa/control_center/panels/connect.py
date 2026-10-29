"""Connect / Status — the read-only front door: check the link, see fw + battery + slots."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                             QPushButton, QFrame, QPlainTextEdit)

from .base import Panel
from .. import constants as K
from ..widgets.ritual_card import RitualCard
from ..engine import screensaver as ss_engine


class ConnectPanel(Panel):
    title = "Connect"
    needs_link = True

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head = QLabel("Connect / Status")
        head.setObjectName("heading")
        root.addWidget(head)

        blurb = QLabel("Screensaver, labels and lights use the remote's CONFIG link. "
                       "Bring it up with the ritual below, then Check link.")
        blurb.setObjectName("dim")
        blurb.setWordWrap(True)
        root.addWidget(blurb)

        # status card
        card = QFrame()
        card.setObjectName("card")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        self.vals = {}
        rows = [("Link", "link"), ("Main firmware", "main"), ("Screen firmware", "screen"),
                ("Battery", "batt"), ("Screensaver slots", "slots")]
        for r, (label, key) in enumerate(rows):
            lab = QLabel(label + ":")
            lab.setObjectName("subheading")
            val = QLabel("—")
            self.vals[key] = val
            grid.addWidget(lab, r, 0, alignment=Qt.AlignmentFlag.AlignRight)
            grid.addWidget(val, r, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(card)

        # buttons
        btns = QHBoxLayout()
        self.check_btn = QPushButton("Check link")
        self.check_btn.setObjectName("primary")
        self.check_btn.clicked.connect(self.win.refresh_link)
        self.deep_btn = QPushButton("Deep status (~30s)")
        self.deep_btn.clicked.connect(self._deep_status)
        btns.addWidget(self.check_btn)
        btns.addWidget(self.deep_btn)
        btns.addStretch()
        root.addLayout(btns)

        # ritual
        self.ritual = RitualCard()
        root.addWidget(self.ritual)

        # deep-status console
        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setPlaceholderText("Deep status output (all screen registers) appears here.")
        root.addWidget(self.console, 1)

        addr = QLabel(f"remote {K.ADDR} · targets screen fw v{K.SCREEN_FW_VERSION}")
        addr.setObjectName("dim")
        root.addWidget(addr)

    def on_link(self, info):
        if not info:
            for v in self.vals.values():
                v.setText("—")
            self.vals["link"].setText("✗ down")
            self.vals["link"].setStyleSheet("color:#f38ba8;")
            self.ritual.show()
            return
        self.ritual.hide()
        sv = info.get("screen_version")
        b = info.get("battery_pct")
        self.vals["link"].setText("✓ up")
        self.vals["link"].setStyleSheet("color:#a6e3a1;")
        self.vals["main"].setText(str(info["main_version"]))
        self.vals["screen"].setText(f"v{sv}" if sv is not None else "v? (screen asleep)")
        self.vals["batt"].setText(f"{b}%" if b is not None else "?")
        self.vals["slots"].setText(info.get("slot_map_str") or "?")

    def _deep_status(self):
        self.console.setPlainText("reading all registers …")
        self.run_ble(ss_engine.full_status, on_done=self._show_status, busy="deep status…")

    def _show_status(self, st):
        lines = [f"{k:16}: {v}" for k, v in st.items()]
        self.console.setPlainText("\n".join(lines))
        self.status("deep status complete")
