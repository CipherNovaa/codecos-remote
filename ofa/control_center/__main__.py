"""Entry point — install the qasync loop so Qt and asyncio share one event loop."""
import asyncio
import sys

from PyQt6.QtWidgets import QApplication


def main():
    import qasync
    from .app import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("OFA Remote Control Center")

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    win = MainWindow()
    win.show()

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
