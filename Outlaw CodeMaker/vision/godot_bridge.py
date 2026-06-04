"""Godot Engine bridge — window detection, monitor-aware capture, OCR-driven error reads.

Detection strategy:
  1. Find any window whose title matches `godot_window_title_match` (default "Godot").
  2. Prefer the editor over running game instances (longer title heuristic).
  3. Capture the *monitor containing the Godot window*, not just the window rect.
     This gives the model peripheral context (e.g., FileSystem dock vs Output dock)
     even when Godot is on a secondary screen.
  4. Run OCR on a tighter window-only crop for text-heavy operations like error reads.

Backend abstraction
-------------------
``pygetwindow`` only works on Windows (and Mac, sort of). On Linux we shell out
to ``xdotool`` instead, which is already a system dependency for ``pyautogui``
in Outlaw OS (added to packages.x86_64 during the CodeMaker deployment slice).
The two backends are wire-compatible: both produce ``_Win`` namedtuples with
the title/geometry/visibility the bridge needs, so the rest of this module
doesn't care which one is in play.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from .vision_engine import VisionEngine


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Window descriptor — the common contract between backends
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Win:
    """Minimal window descriptor. Both backends return this shape."""
    title: str
    left: int
    top: int
    width: int
    height: int
    handle: int | None  # HWND on Windows, X11 window id on Linux, None elsewhere

    @property
    def visible(self) -> bool:
        # The backends only ever surface visible windows, so this is True by
        # construction. Kept as a property so callers reading the old
        # pygetwindow shape keep working.
        return True


# ---------------------------------------------------------------------------
# Windows backend — pygetwindow (the original implementation)
# ---------------------------------------------------------------------------


class _WindowsBackend:
    """Wraps pygetwindow. Only used when ``sys.platform == 'win32'``."""

    def __init__(self):
        import pygetwindow as gw  # local import keeps Linux startup snappy
        self._gw = gw

    def search(self) -> list[_Win]:
        try:
            windows = self._gw.getAllWindows()
        except Exception as exc:  # noqa: BLE001
            logger.warning("pygetwindow failed: %s", exc)
            return []
        out: list[_Win] = []
        for w in windows:
            if not w.title or not w.visible:
                continue
            if w.width <= 100 or w.height <= 100:
                continue
            out.append(_Win(
                title=w.title,
                left=int(w.left), top=int(w.top),
                width=int(w.width), height=int(w.height),
                handle=getattr(w, "_hWnd", None),
            ))
        return out

    def active_title(self) -> str:
        try:
            active = self._gw.getActiveWindow()
            return active.title if active else "(no active window)"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def focus(self, win: _Win) -> str:
        try:
            # We need to call back into the underlying object for restore/activate.
            # Re-search for the live pygetwindow window by handle so isMinimized / activate work.
            live = next(
                (w for w in self._gw.getAllWindows()
                 if getattr(w, "_hWnd", None) == win.handle),
                None,
            )
            if live is None:
                return "ERROR: lost window between search and focus"
            if live.isMinimized:
                live.restore()
            live.activate()
            time.sleep(0.2)
            return f"focused: {live.title}"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR focusing (may be elevation-blocked): {exc}"


# ---------------------------------------------------------------------------
# Linux X11 backend — xdotool subprocess
# ---------------------------------------------------------------------------


def _xdotool_available() -> bool:
    return shutil.which("xdotool") is not None


def _run_xdotool(args: list[str], timeout: float = 2.0) -> tuple[int, str, str]:
    """Run xdotool, returning (exit_code, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(
            ["xdotool", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("xdotool %s failed: %s", args, exc)
        return 1, "", str(exc)


class _LinuxBackend:
    """xdotool-driven window finder for X11. Honors `_NET_WM_*` window
    properties via xdotool so it works under any sane WM (Mutter, KWin,
    Openbox, picom-fronted XFCE, etc.)."""

    def __init__(self, title_match: str):
        self.title_match = title_match
        if not _xdotool_available():
            raise RuntimeError(
                "xdotool not found on PATH. Install it: pacman -S xdotool. "
                "It's pre-installed on Outlaw OS — this only happens during "
                "off-OS development."
            )

    def _list_ids(self) -> list[int]:
        # --name picks by title; without --onlyvisible we'd pick up hidden ones.
        code, out, _ = _run_xdotool([
            "search", "--onlyvisible", "--name", self.title_match,
        ])
        if code != 0 or not out:
            return []
        ids: list[int] = []
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                ids.append(int(line))
        return ids

    def _geometry(self, wid: int) -> tuple[int, int, int, int] | None:
        # --shell emits POSIX shell key=value pairs we can grep without a parser.
        code, out, _ = _run_xdotool(["getwindowgeometry", "--shell", str(wid)])
        if code != 0 or not out:
            return None
        parts: dict[str, int] = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if v.strip().isdigit():
                    parts[k.strip()] = int(v.strip())
        try:
            return parts["X"], parts["Y"], parts["WIDTH"], parts["HEIGHT"]
        except KeyError:
            return None

    def _title(self, wid: int) -> str:
        code, out, _ = _run_xdotool(["getwindowname", str(wid)])
        return out if code == 0 else ""

    def search(self) -> list[_Win]:
        ids = self._list_ids()
        wins: list[_Win] = []
        for wid in ids:
            geom = self._geometry(wid)
            if not geom:
                continue
            x, y, w, h = geom
            if w <= 100 or h <= 100:
                continue
            title = self._title(wid) or ""
            if self.title_match.lower() not in title.lower():
                # xdotool's --name is a regex over WM_NAME; some Godot windows
                # show up with weird unicode that doesn't match cleanly. Skip
                # the ones whose readable title disagrees with our search.
                continue
            wins.append(_Win(
                title=title, left=x, top=y, width=w, height=h, handle=wid,
            ))
        return wins

    def active_title(self) -> str:
        code, out, _ = _run_xdotool(["getactivewindow"])
        if code != 0 or not out.isdigit():
            return "(no active window)"
        title = self._title(int(out))
        return title or "(no active window)"

    def focus(self, win: _Win) -> str:
        if not win.handle:
            return "ERROR: no X11 window id"
        # windowactivate also handles minimized windows on most WMs.
        code, _out, err = _run_xdotool(["windowactivate", "--sync", str(win.handle)])
        if code != 0:
            return f"ERROR focusing: {err or 'xdotool returned non-zero'}"
        time.sleep(0.2)
        return f"focused: {win.title}"


# ---------------------------------------------------------------------------
# Public bridge — backend-selected at construction
# ---------------------------------------------------------------------------


class GodotBridge:
    def __init__(self, vision_config: dict, vision: VisionEngine):
        self.config = vision_config
        self.vision = vision
        self.title_match = vision_config.get("godot_window_title_match", "Godot")
        self._backend = self._build_backend()

    def _build_backend(self):
        """Pick a window backend for the current platform. None means
        windowing isn't available here (e.g., Wayland sessions without
        xdotool, headless CI); the bridge degrades to clear ERRORs."""
        try:
            if sys.platform == "win32":
                return _WindowsBackend()
            if sys.platform.startswith("linux") and _xdotool_available():
                return _LinuxBackend(self.title_match)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Window backend init failed (%s) — bridge degraded.", exc)
        return None

    # ----- window discovery ----------------------------------------------

    def _find_window(self) -> _Win | None:
        if self._backend is None:
            return None
        candidates = self._backend.search()
        # Filter by title match (the Linux backend already filters, but the
        # Windows one passes everything through; this is the canonical filter).
        filtered = [
            w for w in candidates if self.title_match.lower() in w.title.lower()
        ]
        if not filtered:
            return None
        # Prefer the editor — its title is typically longer ("MyGame - res://… - Godot Engine").
        filtered.sort(key=lambda w: -len(w.title))
        return filtered[0]

    def find_hwnd(self) -> int | None:
        """Native window handle. HWND on Windows, X11 window id on Linux. Used
        by the elevation check; None on Linux means the check no-ops, which is
        correct (UIPI is Windows-only)."""
        win = self._find_window()
        return win.handle if win else None

    def active_window_title(self) -> str:
        if self._backend is None:
            return "ERROR: no window backend on this platform"
        return self._backend.active_title()

    def focus_window(self) -> str:
        win = self._find_window()
        if not win:
            return "ERROR: no Godot window found"
        if self._backend is None:
            return "ERROR: no window backend"
        return self._backend.focus(win)

    # ----- capture --------------------------------------------------------

    def window_rect(self) -> tuple[int, int, int, int] | None:
        win = self._find_window()
        if not win:
            return None
        return (
            max(0, win.left), max(0, win.top),
            max(1, win.width), max(1, win.height),
        )

    def capture(self, save_path: str | None = None) -> str:
        """Capture the whole monitor that contains Godot (gives the model context)."""
        win = self._find_window()
        if not win:
            return "ERROR: no Godot window found"
        rect = (
            max(0, win.left), max(0, win.top),
            max(1, win.width), max(1, win.height),
        )
        img = self.vision.capture_monitor_for_rect(rect, save_path=save_path)
        h, w = img.shape[:2]
        return (
            f"captured monitor containing '{win.title}' ({w}x{h})"
            + (f" -> {save_path}" if save_path else "")
        )

    def capture_window_only(self, save_path: str | None = None) -> str:
        """Tight capture of just the Godot window (for OCR or template matching)."""
        rect = self.window_rect()
        if not rect:
            return "ERROR: no Godot window found"
        self.vision.capture_region(*rect, save_path=save_path)
        return f"captured Godot window {rect[2]}x{rect[3]}"

    def ocr_text(self) -> str:
        rect = self.window_rect()
        if not rect:
            return "ERROR: no Godot window found"
        img = self.vision.capture_region(*rect)
        text = self.vision.ocr(img)
        red_alert = self.vision.detect_red_error_text(img)
        prefix = "[RED-TEXT DETECTED — likely script error]\n" if red_alert else ""
        return prefix + text.strip()
