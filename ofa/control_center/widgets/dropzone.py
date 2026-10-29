"""Drag-and-drop image target (click to browse) — ported from supernote_ref.py."""
from pathlib import Path

from .. import constants as K

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QFileDialog

_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif")


class DropZone(QLabel):
    """Emits file_chosen(str) with a single image path (drop or browse)."""
    file_chosen = pyqtSignal(str)

    def __init__(self, text="Drop an image here\nor click to browse"):
        super().__init__(text)
        self.setObjectName("drop_zone")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(96)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setWordWrap(True)

    def mousePressEvent(self, _event):
        start = str(K.FINALISTS_DIR)
        if not Path(start).exists():
            start = str(Path.home())
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select an image", start,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.gif)")
        if files:
            self.file_chosen.emit(files[0])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for u in event.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(_EXTS):
                self.file_chosen.emit(p)
                return
