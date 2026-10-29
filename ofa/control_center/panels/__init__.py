"""Feature panels (one QWidget tab each) + the PANELS registry.

Add a feature = add a panel class here and list it in PANELS; the app builds a tab for
each. Every panel gets the MainWindow as ``win`` for shared services (async runner,
status bar, link cache, ritual card).
"""
from .connect import ConnectPanel
from .screensaver import ScreensaverPanel
from .labels import LabelsPanel
from .lights import LightsPanel
from .amp import AmpPanel
from .firmware import FirmwarePanel
from .logs import LogsPanel

PANELS = [
    ConnectPanel,
    ScreensaverPanel,
    LabelsPanel,
    LightsPanel,
    AmpPanel,
    FirmwarePanel,
    LogsPanel,
]
