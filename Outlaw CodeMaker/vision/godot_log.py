"""Self-Healing watcher — detects Godot errors and feeds them to the agent.

Two detection paths (user chose "both"):
  1. If a Godot log file path is configured, tail it and flag new ERROR lines
     (precise — full error text incl. file:line).
  2. Otherwise capture the Godot window and use the red-text heuristic; if red
     error text is on screen, OCR it (works out of the box, no config).

Runs on its own QThread, sleeping in 500 ms slices so Stop is instant. De-dupes
so the same error isn't reported repeatedly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


ERROR_MARKERS = (
    "SCRIPT ERROR", "ERROR:", "TRACEBACK", "PARSE ERROR", "PARSER ERROR",
    "INVALID CALL", "CANNOT CALL", "NONEXISTENT FUNCTION", "STACK FRAMES",
    "EXPECTED", "INVALID GET INDEX",
)


def is_error_line(line: str) -> bool:
    up = line.upper()
    return any(marker in up for marker in ERROR_MARKERS)


class GodotErrorWatcher(QThread):
    error_detected = pyqtSignal(str, str)  # (source, text)
    status = pyqtSignal(str)

    def __init__(self, godot_bridge, log_path: str = "", poll_seconds: int = 8):
        super().__init__()
        self.godot = godot_bridge
        self.log_path = log_path or ""
        self.poll_seconds = max(3, int(poll_seconds))
        self._last_sig: str | None = None
        self._log_pos = 0

    def run(self) -> None:
        # Start at the end of the log so we only react to NEW errors.
        if self.log_path and Path(self.log_path).exists():
            try:
                self._log_pos = Path(self.log_path).stat().st_size
            except OSError:
                self._log_pos = 0
        self.status.emit(
            f"self-heal watching {'log: ' + self.log_path if self.log_path else 'Godot window (OCR)'}"
        )

        while not self.isInterruptionRequested():
            try:
                if self.log_path:
                    self._check_log()
                else:
                    self._check_ocr()
            except Exception as exc:  # noqa: BLE001
                self.status.emit(f"watcher error: {exc}")
            # responsive sleep
            for _ in range(self.poll_seconds * 2):
                if self.isInterruptionRequested():
                    return
                self.msleep(500)

    def _emit_once(self, source: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        sig = hashlib.md5(text.encode("utf-8", "replace")).hexdigest()
        if sig != self._last_sig:
            self._last_sig = sig
            self.error_detected.emit(source, text)

    def _check_log(self) -> None:
        p = Path(self.log_path)
        if not p.exists():
            return
        size = p.stat().st_size
        if size < self._log_pos:      # file rotated/truncated
            self._log_pos = 0
        if size == self._log_pos:
            return
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self._log_pos)
            new_text = fh.read()
            self._log_pos = fh.tell()
        errors = [ln for ln in new_text.splitlines() if is_error_line(ln)]
        if errors:
            self._emit_once("godot.log", "\n".join(errors[-20:]))

    def _check_ocr(self) -> None:
        rect = self.godot.window_rect()
        if not rect:
            return
        img = self.godot.vision.capture_region(*rect)
        if self.godot.vision.detect_red_error_text(img):
            text = self.godot.vision.ocr(img)
            self._emit_once("godot.ocr", text or "[red error text detected on the Godot screen]")
