"""boot_restore.py — Outlaw CodeMaker's anti-bricking launcher.

Zero dependencies (stdlib only) so it can ALWAYS run, even if a bad evolution
breaks the main app's imports. It is the process that run.bat actually starts.

Logic:
  1. Read the consecutive-failure counter (.launch_state).
  2. If it's >= 3, the last few launches crashed before the UI came up — assume a
     bad evolution and RECOVER: `git reset --hard HEAD` (the Anchor snapshot is the
     last commit, taken *before* any swap), with a backup-zip restore as fallback.
  3. Increment the counter (pending), launch main.py, and wait.
  4. main.py calls record_success() ~3s after the window shows, zeroing the counter.

So three boots that never reach a healthy UI trigger an automatic rollback.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / ".launch_state"
MAX_FAILS = 3


def read_fails() -> int:
    try:
        return int(json.loads(STATE.read_text())["fails"])
    except Exception:
        return 0


def write_fails(n: int) -> None:
    try:
        STATE.write_text(json.dumps({"fails": max(0, n)}))
    except Exception:
        pass


def record_attempt() -> int:
    n = read_fails() + 1
    write_fails(n)
    return n


def record_success() -> None:
    """Called by the running app once the UI is healthy."""
    write_fails(0)


def _git_reset() -> bool:
    try:
        # safe.directory=* lets git operate on drives without ownership records (e.g. F:).
        r = subprocess.run(
            ["git", "-c", "safe.directory=*", "reset", "--hard", "HEAD"],
            cwd=str(HERE), capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0
    except Exception:
        return False


def _restore_latest_backup() -> bool:
    backups = sorted((HERE / "backups").glob("outlaw_*.zip"), reverse=True)
    if not backups:
        return False
    try:
        with zipfile.ZipFile(backups[0]) as zf:
            zf.extractall(HERE)
        return True
    except Exception:
        return False


def recover() -> str:
    if _git_reset():
        return "git reset --hard HEAD"
    if _restore_latest_backup():
        return "restored newest backup zip"
    return "RECOVERY FAILED — restore manually (see backups/ or `git reflog`)"


def main() -> int:
    fails = read_fails()
    if fails >= MAX_FAILS:
        print(f"[boot_restore] {fails} failed launches in a row — rolling back…", flush=True)
        print(f"[boot_restore] {recover()}", flush=True)
        write_fails(0)

    record_attempt()  # cleared by record_success() once the UI is healthy
    try:
        return subprocess.call([sys.executable, "main.py"], cwd=str(HERE))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
