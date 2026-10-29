"""Six-slot screensaver grid with per-slot thumbnail, name/mode, and device state."""
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QGridLayout, QLabel, QWidget

from .. import constants as K

_THUMB_W = 130
_THUMB_H = round(_THUMB_W * 384 / 168)


class SlotTile(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, slot):
        super().__init__()
        self.slot = slot
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(_THUMB_H + 96)     # deterministic: all tiles equal height

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(3)

        self.head = QLabel(f"slot {slot}")
        self.head.setObjectName("subheading")
        lay.addWidget(self.head)

        self.thumb = QLabel()
        self.thumb.setFixedSize(_THUMB_W, _THUMB_H)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet("background:#0b0b12; border-radius:3px;")
        lay.addWidget(self.thumb, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.name = QLabel("(empty)")
        self.name.setWordWrap(True)
        self.name.setFixedHeight(34)
        self.name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.name)

        self.detail = QLabel("")
        self.detail.setObjectName("dim")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.detail)

        self.state = QLabel("device: ?")
        self.state.setObjectName("dim")
        self.state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.state)
        lay.addStretch()                          # keep content pinned to the top

    def mousePressEvent(self, _e):
        self.clicked.emit(self.slot)

    def set_selected(self, on):
        self._selected = on
        self.setStyleSheet("QFrame#card{border:2px solid #89b4fa;}" if on else "")

    def set_thumb(self, pix: QPixmap):
        if pix and not pix.isNull():
            self.thumb.setPixmap(pix.scaled(self.thumb.size(),
                                            Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.FastTransformation))
        else:
            self.thumb.clear()

    def set_record(self, entry, present):
        if entry:
            self.name.setText(Path(entry["path"]).name)
            inv = " · inv" if entry.get("invert") else ""
            self.detail.setText(f"{entry.get('mode', 'cover')}/{entry.get('method', 'threshold')}{inv}")
        else:
            self.name.setText("(not recorded)")
            self.detail.setText("")
            self.thumb.clear()
        if present is None:
            self.state.setText("device: ?")
        elif present:
            self.state.setText("● PRESENT")
            self.state.setStyleSheet("color:#a6e3a1;")
        else:
            self.state.setText("○ empty")
            self.state.setStyleSheet("")


class SlotGrid(QWidget):
    slot_selected = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.selected = 0
        grid = QGridLayout(self)
        grid.setSpacing(8)
        self.tiles = []
        for i in range(K.NSLOTS):
            t = SlotTile(i)
            t.clicked.connect(self._on_click)
            self.tiles.append(t)
            grid.addWidget(t, i // 3, i % 3)
        self.tiles[0].set_selected(True)

    def _on_click(self, slot):
        self.select(slot)
        self.slot_selected.emit(slot)

    def select(self, slot):
        self.selected = slot
        for t in self.tiles:
            t.set_selected(t.slot == slot)

    def update_records(self, lineup, present_list):
        slots = (lineup or {}).get("slots", {})
        for i, t in enumerate(self.tiles):
            present = present_list[i] if present_list and i < len(present_list) else None
            t.set_record(slots.get(str(i)), present)
