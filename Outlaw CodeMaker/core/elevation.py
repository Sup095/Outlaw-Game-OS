"""Windows integrity-level utilities + UAC relaunch.

Strategy chosen by the user: *Detect & re-launch if needed*.
We start unelevated. When the agent needs to interact with an elevated target
(typically the Godot editor launched as admin), we detect the integrity mismatch
and offer a re-launch via ShellExecuteW with the "runas" verb (which fires the
standard UAC consent prompt).

Why this matters on Windows:
    User Interface Privilege Isolation (UIPI) blocks SendInput from a
    lower-integrity process to a higher-integrity window. SetForegroundWindow
    also silently fails. So if Godot is running as admin and we are not,
    AutomationTool.click/type will look like it worked but nothing happens.

This module is import-safe on non-Windows (everything degrades to "MEDIUM" / no-op),
so dev on macOS/Linux doesn't blow up.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from enum import IntEnum
from pathlib import Path


logger = logging.getLogger(__name__)


class IntegrityLevel(IntEnum):
    UNKNOWN  = -1
    LOW      = 0x1000
    MEDIUM   = 0x2000
    HIGH     = 0x3000
    SYSTEM   = 0x4000

    @property
    def label(self) -> str:
        return self.name.title()


# Sentinel for non-Windows platforms so the rest of the app stays functional
_IS_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Win32 constants / structures
# ---------------------------------------------------------------------------

TOKEN_QUERY = 0x0008
TokenIntegrityLevel = 25
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

SECURITY_MANDATORY_LOW_RID    = 0x00001000
SECURITY_MANDATORY_MEDIUM_RID = 0x00002000
SECURITY_MANDATORY_HIGH_RID   = 0x00003000
SECURITY_MANDATORY_SYSTEM_RID = 0x00004000


# Explicit signatures — on 64-bit Python, default int restype truncates pointers.
if _IS_WINDOWS:
    _advapi32 = ctypes.windll.advapi32
    _kernel32 = ctypes.windll.kernel32
    _user32   = ctypes.windll.user32
    _shell32  = ctypes.windll.shell32

    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL

    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL

    _advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    _advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)

    _advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    _shell32.IsUserAnAdmin.restype = wintypes.BOOL
    _shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
    ]
    _shell32.ShellExecuteW.restype = wintypes.HINSTANCE


# ---------------------------------------------------------------------------
# Self elevation
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    """True iff current process has High or System integrity."""
    if not _IS_WINDOWS:
        return False
    try:
        return bool(_shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def self_integrity_level() -> IntegrityLevel:
    if not _IS_WINDOWS:
        return IntegrityLevel.MEDIUM
    try:
        hproc = _kernel32.GetCurrentProcess()
        return _integrity_for_process(hproc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("self integrity check failed: %s", exc)
        return IntegrityLevel.UNKNOWN


# ---------------------------------------------------------------------------
# Other-process / window integrity
# ---------------------------------------------------------------------------

def window_integrity_level(hwnd: int | None) -> IntegrityLevel:
    """Integrity of the process that owns the given window handle."""
    if not _IS_WINDOWS or not hwnd:
        return IntegrityLevel.UNKNOWN
    try:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        if not pid.value:
            return IntegrityLevel.UNKNOWN
        hproc = _kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not hproc:
            # Access denied = target is higher integrity (UIPI). Treat as HIGH.
            return IntegrityLevel.HIGH
        try:
            return _integrity_for_process(hproc)
        finally:
            _kernel32.CloseHandle(hproc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("window integrity probe failed: %s", exc)
        return IntegrityLevel.UNKNOWN


def _integrity_for_process(hproc) -> IntegrityLevel:
    """Open the process token and read TokenIntegrityLevel."""
    htoken = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(htoken)):
        return IntegrityLevel.UNKNOWN
    try:
        size = wintypes.DWORD()
        # First call: ask for required size. Expected to fail with insufficient buffer.
        _advapi32.GetTokenInformation(
            htoken, TokenIntegrityLevel, None, 0, ctypes.byref(size)
        )
        if size.value == 0:
            return IntegrityLevel.UNKNOWN

        buf = (ctypes.c_byte * size.value)()
        if not _advapi32.GetTokenInformation(
            htoken, TokenIntegrityLevel, buf, size.value, ctypes.byref(size)
        ):
            return IntegrityLevel.UNKNOWN

        # TOKEN_MANDATORY_LABEL begins with SID_AND_ATTRIBUTES { PSID Sid; DWORD Attrs; }
        # First pointer-sized field is the PSID.
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        if not psid:
            return IntegrityLevel.UNKNOWN

        count = _advapi32.GetSidSubAuthorityCount(psid)[0]
        rid = _advapi32.GetSidSubAuthority(psid, count - 1)[0]

        if rid >= SECURITY_MANDATORY_SYSTEM_RID:
            return IntegrityLevel.SYSTEM
        if rid >= SECURITY_MANDATORY_HIGH_RID:
            return IntegrityLevel.HIGH
        if rid >= SECURITY_MANDATORY_MEDIUM_RID:
            return IntegrityLevel.MEDIUM
        return IntegrityLevel.LOW
    finally:
        _kernel32.CloseHandle(htoken)


# ---------------------------------------------------------------------------
# Relaunch
# ---------------------------------------------------------------------------

def relaunch_as_admin(argv: list[str] | None = None) -> bool:
    """Re-launch the current python script with elevation.

    Returns True if the relaunch was kicked off (caller should exit).
    Returns False if user denied UAC or platform is not Windows.
    """
    if not _IS_WINDOWS:
        return False
    if is_admin():
        return False

    argv = argv or sys.argv
    python_exe = sys.executable
    params = " ".join(f'"{a}"' for a in argv)

    SW_NORMAL = 1
    try:
        rc = _shell32.ShellExecuteW(
            None, "runas", python_exe, params, str(Path.cwd()), SW_NORMAL
        )
        # ShellExecuteW returns >32 on success; <=32 indicates an error (1223 = cancelled)
        return int(rc) > 32
    except Exception as exc:  # noqa: BLE001
        logger.error("relaunch_as_admin failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# High-level helpers used by the rest of the app
# ---------------------------------------------------------------------------

def elevation_mismatch(target_hwnd: int | None) -> bool:
    """True iff we are lower integrity than the target window (input will be blocked)."""
    if not target_hwnd:
        return False
    me = self_integrity_level()
    them = window_integrity_level(target_hwnd)
    if me == IntegrityLevel.UNKNOWN or them == IntegrityLevel.UNKNOWN:
        return False
    return int(them) > int(me)


def status_string() -> str:
    me = self_integrity_level()
    return f"{me.label}{' (admin)' if is_admin() else ''}"
