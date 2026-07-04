"""Install an Arch package from the Dev session.

Uses the SAME privileged helper the desktop shell uses —
``pkexec /usr/local/bin/outlaw-pkg-install <pkg>`` — which enables multilib,
inits the keyring, syncs, and runs ``pacman -S --needed --noconfirm``. The
49-outlaw polkit rule makes that helper prompt-free for the operator (wheel), so
this is a one-click install. A small live-log dialog lets the user watch the
download; ``install_package`` returns whether it succeeded and fails soft when
the helper or pkexec isn't available (e.g. running off the real OS).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from .styles import COLORS

HELPER = "/usr/local/bin/outlaw-pkg-install"

# Keep-alive for install workers whose dialog was closed mid-run. The dialog
# deliberately doesn't kill pacman (interrupting a transaction is unsafe), so the
# QThread keeps running after the dialog is gone. Without a strong reference here,
# Python would GC the running QThread and Qt aborts the whole app with
# "QThread: Destroyed while thread is still running". Workers remove themselves
# on `done`.
_ALIVE_WORKERS: set = set()


class _InstallWorker(QThread):
    """Runs the privileged installer, streaming its output line by line."""

    line = pyqtSignal(str)
    done = pyqtSignal(int)  # process exit code

    def __init__(self, pkg: str):
        super().__init__()
        self._pkg = pkg

    def run(self):  # noqa: D401 — QThread entry point
        cmd = ["pkexec", HELPER, self._pkg]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except OSError as exc:
            self.line.emit(f"Could not start the installer: {exc}")
            self.done.emit(127)
            return
        try:
            assert proc.stdout is not None
            for ln in proc.stdout:
                self.line.emit(ln.rstrip())
        except Exception:  # noqa: BLE001 — never let stream reading crash the thread
            pass
        proc.wait()
        self.done.emit(proc.returncode if proc.returncode is not None else 1)


class _InstallDialog(QDialog):
    def __init__(self, pkg: str, label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Install {label}")
        self.resize(580, 440)
        self.success = False
        self._label = label

        head = QLabel(f"Downloading and installing {label}…")
        head.setStyleSheet(f"color: {COLORS['accent']}; font-size: 12pt; font-weight: 700;")
        sub = QLabel("This can take a few minutes depending on your connection. "
                     "You can keep setting up while it runs.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {COLORS['muted']}; font-size: 9pt;")

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # indeterminate until done
        self.bar.setTextVisible(False)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(
            f"QTextEdit {{ background: {COLORS['code_bg']}; color: {COLORS['muted']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 6px;"
            f" font-size: 9.5pt; }}"
        )

        self.close_btn = QPushButton("Cancel")
        self.close_btn.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.close_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.addWidget(head)
        lay.addWidget(sub)
        lay.addWidget(self.bar)
        lay.addWidget(self.log, 1)
        lay.addLayout(row)

        self._worker = _InstallWorker(pkg)
        self._worker.line.connect(self._append)
        self._worker.done.connect(self._finish)
        self._worker.start()

    def _append(self, ln: str) -> None:
        if not ln:
            return
        self.log.append(ln)
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.log.setTextCursor(cur)
        self.log.ensureCursorVisible()

    def _finish(self, code: int) -> None:
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.success = (code == 0)
        if self.success:
            self._append(f"\n✓ {self._label} installed.")
        else:
            self._append(f"\n✗ Install failed (exit {code}). You can also install it from "
                         "the Desktop session's Apps page.")
        self.close_btn.setText("Close")
        try:
            self.close_btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.close_btn.clicked.connect(self.accept)

    def closeEvent(self, e) -> None:
        # If the user closes mid-install we stop watching, but DON'T kill pacman
        # (interrupting a package transaction is unsafe) — it finishes in the
        # background and the app will be there next time they click.
        w = self._worker
        if w is not None and w.isRunning():
            try:
                w.line.disconnect()
            except (TypeError, RuntimeError):
                pass
            # Hand the still-running thread to a module-level keep-alive so Python
            # can't GC it (which would abort the app). It drops itself once the
            # pacman process exits. Reconnect `done` to the cleanup only.
            try:
                w.done.disconnect()
            except (TypeError, RuntimeError):
                pass
            _ALIVE_WORKERS.add(w)
            w.done.connect(lambda _code, _w=w: _ALIVE_WORKERS.discard(_w))
            w.finished.connect(lambda _w=w: _ALIVE_WORKERS.discard(_w))
        super().closeEvent(e)


def install_package(pkg: str, label: str, parent=None) -> bool:
    """Install ``pkg`` via the privileged helper, showing a live-log dialog.

    Returns True on success. Fails soft (an info box, returns False) when the
    helper or pkexec isn't present — e.g. when CodeMaker is run off a non-Outlaw
    machine for development.
    """
    if not shutil.which("pkexec") or not Path(HELPER).exists():
        QMessageBox.information(
            parent, label,
            f"Can't install {label} from here — the Outlaw installer helper isn't "
            "available on this system. Install it from the Desktop session's Apps page instead.",
        )
        return False
    dlg = _InstallDialog(pkg, label, parent)
    dlg.exec()
    return dlg.success
