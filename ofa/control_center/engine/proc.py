"""Generic QProcess runner + journaling.

Used for tools that must stay in their own process: ``flash.py`` (no-rollback,
brick-capable), the ``check_*.py`` validators (heavy unicorn gates), ``labels.py``
(reuse its plan+backup+apply path), and the sudo loggers via ``pkexec``.

Every run journals to ``artifacts/control-center/<stamp>-<action>/`` — ``command.txt``,
a live-appended ``output.log``, and a final ``run.json`` — mirroring ofa_control.py so
there is always a paper trail of what was sent to the remote.
"""
import json
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

from .. import constants as K


def journal_dir(action):
    """Create and return artifacts/control-center/<stamp>-<action>/."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")   # microseconds: no same-second clobber
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in action)[:40]
    d = K.JOURNAL_ROOT / f"{stamp}-{safe}"
    d.mkdir(parents=True, exist_ok=True)
    return d


class ProcRunner(QObject):
    """Run one subprocess, stream its output, journal it, parse JSON progress.

    Signals (all on the Qt thread):
      line(str)              — one line of merged stdout/stderr
      progress(int, int)     — sent, total (from flash.py `transfer_progress`)
      event(dict)            — a parsed line-delimited JSON record (flash.py)
      finished(int, str)     — exit code, full captured output
      failed(str)            — could not start / crashed
    """
    line = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    event = pyqtSignal(dict)
    finished = pyqtSignal(int, str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self._buf = ""
        self._all = []
        self._jdir = None
        self._logfh = None
        self._parse_json = False

    def is_running(self):
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def run(self, argv, action="run", cwd=None, parse_json=False):
        """Start argv (list of str). Journals under <action>. Returns the journal dir."""
        if self.is_running():
            self.failed.emit("a process is already running")
            return None
        self._buf, self._all, self._parse_json = "", [], parse_json
        self._jdir = journal_dir(action)
        try:
            self._logfh = open(self._jdir / "output.log", "w")
        except OSError:
            self._logfh = None
        (self._jdir / "command.txt").write_text(
            " ".join(str(a) for a in argv) + "\n"
            + f"# cwd: {cwd or K.OFA_DIR}\n# started: {datetime.now().isoformat()}\n")

        # NOTE: do NOT parent to `self` — delivering the ChildAdded event to a bare
        # Python QObject subclass aborts under PyQt6 + Python 3.14. Keep it unparented;
        # the self.proc reference keeps it alive for the run's lifetime.
        self.proc = QProcess()
        self.proc.setWorkingDirectory(str(cwd or K.OFA_DIR))
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_error)
        self.proc.start(str(argv[0]), [str(a) for a in argv[1:]])
        return self._jdir

    def kill(self):
        if self.is_running():
            self.proc.kill()

    # ── internals ──
    def _on_output(self):
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        if self._logfh:
            try:
                self._logfh.write(data)
                self._logfh.flush()
            except OSError:
                pass
        self._buf += data
        while "\n" in self._buf:
            ln, self._buf = self._buf.split("\n", 1)
            self._all.append(ln)
            self.line.emit(ln)
            if self._parse_json:
                self._maybe_json(ln)

    def _maybe_json(self, ln):
        s = ln.strip()
        if not (s.startswith("{") and s.endswith("}")):
            return
        try:
            rec = json.loads(s)
        except ValueError:
            return
        self.event.emit(rec)
        if rec.get("event") == "transfer_progress":
            self.progress.emit(int(rec.get("sent", 0)), int(rec.get("total", 0)))

    def _on_finished(self, code, _status):
        if self._buf:                       # flush a trailing partial line
            self._all.append(self._buf)
            self.line.emit(self._buf)
            if self._parse_json:
                self._maybe_json(self._buf)
            self._buf = ""
        full = "\n".join(self._all)
        if self._logfh:
            try:
                self._logfh.close()
            except OSError:
                pass
            self._logfh = None
        if self._jdir:
            try:
                (self._jdir / "run.json").write_text(json.dumps({
                    "exit_code": int(code),
                    "finished": datetime.now().isoformat(),
                    "lines": len(self._all),
                }, indent=2) + "\n")
            except OSError:
                pass
        self.finished.emit(int(code), full)

    def _on_error(self, err):
        self.failed.emit(f"process error: {err}")
