"""Automation tool — PyAutoGUI wrapper.

⚠️ FAILSAFE DISABLED (user-chosen).
The default PyAutoGUI behaviour aborts when the mouse hits (0,0). That has been
turned off. The ONLY way to stop an automation run is the in-app Stop button
(MainWindow.stop_btn) or killing the process.

If you want the corner-failsafe back, set `pyautogui.FAILSAFE = True` below.
"""

from __future__ import annotations

import logging
import time

import pyautogui


logger = logging.getLogger(__name__)


# --- USER-CHOSEN: failsafe OFF; rely on the Stop button only -------------
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05
# -------------------------------------------------------------------------


class AutomationTool:
    def __init__(self, default_delay: float = 0.1):
        self.default_delay = default_delay
        self.screen_w, self.screen_h = pyautogui.size()
        logger.warning(
            "PyAutoGUI failsafe is DISABLED. Stop the agent via the Stop button."
        )

    @staticmethod
    def failsafe_enabled() -> bool:
        return bool(pyautogui.FAILSAFE)

    @staticmethod
    def set_failsafe(enabled: bool) -> None:
        pyautogui.FAILSAFE = bool(enabled)
        logger.info("PyAutoGUI failsafe set to %s", enabled)

    def _in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.screen_w and 0 <= y < self.screen_h

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> str:
        if not self._in_bounds(x, y):
            return f"ERROR: ({x},{y}) outside screen {self.screen_w}x{self.screen_h}"
        pyautogui.click(x=x, y=y, button=button, clicks=clicks)
        time.sleep(self.default_delay)
        return f"clicked {button} @ ({x},{y}) x{clicks}"

    def move(self, x: int, y: int, *, duration: float = 0.2) -> str:
        if not self._in_bounds(x, y):
            return f"ERROR: ({x},{y}) outside screen"
        pyautogui.moveTo(x, y, duration=duration)
        return f"moved to ({x},{y})"

    def type_text(self, text: str, *, interval: float = 0.01) -> str:
        pyautogui.typewrite(text, interval=interval)
        return f"typed {len(text)} chars"

    def hotkey(self, *keys: str) -> str:
        pyautogui.hotkey(*keys)
        time.sleep(self.default_delay)
        return f"hotkey {'+'.join(keys)}"

    def scroll(self, dy: int) -> str:
        pyautogui.scroll(dy)
        return f"scrolled {dy}"
