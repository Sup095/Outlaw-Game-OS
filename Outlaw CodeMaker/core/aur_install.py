"""On-demand AUR package installer.

A handful of Outlaw OS features depend on packages that don't live in the
official Arch repositories — most notably ``steamcmd`` for the Steam publish
flow. Rather than baking an AUR helper into the live ISO, we ship a
single-purpose privileged script at ``/usr/local/bin/outlaw-install-aur``
with a hardcoded allowlist, and call it via ``pkexec`` when the user
explicitly opts into installing one of these packages.

This module is the Python side of that handshake:

* :func:`helper_available` checks whether the privileged helper is present
  + executable (false on non-Outlaw-OS hosts, e.g. dev workstations).
* :func:`install_package` invokes the helper, streams stdout/stderr through
  an optional ``on_line`` callback, and returns a structured result. Never
  raises on install failure — UI code can branch on ``result.ok``.

Threading note: :func:`install_package` blocks until the helper finishes.
Call it from a worker thread (e.g. ``QThread`` / ``QRunnable``) if you
need the UI to stay responsive, or wrap the whole thing in
``QThreadPool.globalInstance().start(...)``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Absolute path to the privileged helper Outlaw OS ships at
# outlaw-installer/airootfs/usr/local/bin/outlaw-install-aur. Hardcoded
# because pkexec resolves paths against polkit rules that key off the exact
# binary path; relying on $PATH would defeat the security model.
HELPER_PATH = "/usr/local/bin/outlaw-install-aur"


@dataclass
class AurInstallResult:
    """Outcome of a single :func:`install_package` call."""

    ok: bool
    package: str
    note: str = ""
    """Short user-facing summary. Always set; safe to display verbatim."""

    log_tail: str = ""
    """Last ~3000 chars of helper stdout/stderr for diagnostics. May be empty
    when the failure happens before the helper produces any output (e.g.
    polkit auth cancelled, helper not found)."""

    exit_code: Optional[int] = None
    """The helper's process exit code, or ``None`` if it never started."""

    @property
    def needs_manual_install(self) -> bool:
        """True when the failure means the user must install the package
        themselves (helper missing, polkit unavailable). UI should show the
        manual-install instructions in this case rather than offering retry.
        """
        return not self.ok and self.exit_code is None


def helper_available() -> bool:
    """True only when the privileged AUR helper script is present + executable.

    Outlaw OS ships it via :data:`HELPER_PATH`. Anywhere else (vanilla Arch,
    macOS dev workstation, etc.) it's absent. Cheap call, safe to invoke from
    UI render paths.
    """
    return os.path.isfile(HELPER_PATH) and os.access(HELPER_PATH, os.X_OK)


def is_already_installed(package: str) -> bool:
    """Quick check: is ``package`` already on the system?

    Uses ``shutil.which`` for binary-name packages (covers ``steamcmd``).
    For library-only packages we'd need ``pacman -Qi`` instead, but for
    every entry on the current allowlist the binary-on-PATH heuristic is
    correct and avoids spawning a subprocess.
    """
    return shutil.which(package) is not None


def install_package(
    package: str,
    on_line: Optional[Callable[[str], None]] = None,
) -> AurInstallResult:
    """Install one allowlisted AUR package via the privileged helper.

    Runs ``pkexec <HELPER_PATH> <package>``. pkexec triggers a polkit
    password prompt the first time the user invokes it in a session; once
    authenticated, polkit caches credentials for a few minutes.

    Parameters
    ----------
    package
        The AUR package name. Must be on the helper's hardcoded allowlist
        (the helper rejects anything else with exit code 3).
    on_line
        Optional callback fired for each output line from the helper.
        Useful for live-updating a progress dialog. Exceptions raised by
        the callback are logged and swallowed so they can't kill the
        install mid-build.

    Returns
    -------
    AurInstallResult
        Always returned — failures are reported in ``.ok`` and ``.note``
        rather than raised.
    """
    if is_already_installed(package):
        return AurInstallResult(
            ok=True,
            package=package,
            note=f"{package} is already installed.",
            exit_code=0,
        )

    if not helper_available():
        return AurInstallResult(
            ok=False,
            package=package,
            note=(
                f"Auto-install isn't available on this host (looking for "
                f"{HELPER_PATH}). This is expected on a non-Outlaw-OS "
                f"machine. Install '{package}' from AUR manually with "
                f"`yay -S {package}` (or `paru -S {package}`), then click "
                f"the refresh button."
            ),
            exit_code=None,
        )

    # pkexec needs to be on PATH for the action to succeed at all. The
    # binary ships with the polkit package, which is in our base set; this
    # check catches dev hosts where polkit isn't installed.
    if shutil.which("pkexec") is None:
        return AurInstallResult(
            ok=False,
            package=package,
            note=(
                "pkexec not found — polkit isn't installed on this host. "
                "Install the polkit package and try again."
            ),
            exit_code=None,
        )

    cmd = ["pkexec", HELPER_PATH, package]
    output_lines: list[str] = []

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,        # line-buffered so progress feels live
        )
    except OSError as exc:
        logger.exception("Failed to spawn pkexec for AUR install")
        return AurInstallResult(
            ok=False,
            package=package,
            note=f"Could not spawn pkexec ({exc}).",
            exit_code=None,
        )

    assert proc.stdout is not None
    for line in proc.stdout:
        output_lines.append(line)
        if on_line is not None:
            try:
                on_line(line.rstrip("\n"))
            except Exception:
                # Don't let a UI bug kill the install.
                logger.exception("on_line callback failed")

    rc = proc.wait()
    tail = "".join(output_lines)[-3000:]

    if rc == 0:
        return AurInstallResult(
            ok=True,
            package=package,
            note=f"Installed {package} from AUR.",
            log_tail=tail,
            exit_code=0,
        )

    # pkexec's own failure codes (see polkit.org docs):
    #   126 — the user cancelled the auth prompt
    #   127 — the binary couldn't be executed at all
    # Anything else came from our helper script — its exit codes are
    # documented in outlaw-install-aur itself (2=usage, 3=allowlist,
    # 4=network/clone, 5=build).
    if rc == 126:
        note = "Authorization cancelled."
    elif rc == 127:
        note = "pkexec couldn't execute the helper (path or permissions issue)."
    elif rc == 3:
        note = (
            f"'{package}' isn't on the Outlaw AUR allowlist. This is a "
            "CodeMaker bug — report it."
        )
    elif rc == 4:
        note = (
            f"Could not download {package} from AUR. Check your network "
            "connection and try again."
        )
    elif rc == 5:
        note = (
            f"Building {package} failed. See the log below for details — "
            "the AUR PKGBUILD may need an update."
        )
    else:
        note = f"Helper exited with code {rc}. See log for details."

    return AurInstallResult(
        ok=False,
        package=package,
        note=note,
        log_tail=tail,
        exit_code=rc,
    )
