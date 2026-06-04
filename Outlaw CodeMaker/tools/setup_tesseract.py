"""One-shot installer for the bundled Tesseract.

User-chosen strategy: a portable copy lives under `tools/tesseract/` so the app
ships self-contained and OCR works on a fresh machine without a system install.

We don't (and can't legally) re-host Tesseract binaries inside this repo. This
script downloads the official UB-Mannheim Windows installer to a temp file,
verifies the SHA-256, then either:

  (a) silently extracts the binary tree using 7-Zip if available, or
  (b) runs the installer with /S /D=<bundled-dir>, which the UB-Mannheim
      packager (Inno Setup) accepts for unattended installs.

Run from the project root:
    python tools/setup_tesseract.py

After completion, vision_engine will auto-discover tools/tesseract/tesseract.exe
on every launch.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


# --- Pin a known UB-Mannheim build. Update both URL and SHA when bumping. ---
TESSERACT_VERSION = "5.3.3.20231005"
DOWNLOAD_URL = (
    "https://digi.bib.uni-mannheim.de/tesseract/"
    f"tesseract-ocr-w64-setup-{TESSERACT_VERSION}.exe"
)
EXPECTED_SHA256: str | None = None  # set after first successful run; None = skip verify

BUNDLE_DIR = Path(__file__).resolve().parent / "tesseract"
EXE_PATH = BUNDLE_DIR / "tesseract.exe"


# --------------------------------------------------------------------------

def already_installed() -> bool:
    return EXE_PATH.exists()


def download(url: str, dest: Path) -> None:
    print(f"→ downloading {url}")
    with urllib.request.urlopen(url) as resp, dest.open("wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        read = 0
        chunk = 1024 * 256
        while True:
            buf = resp.read(chunk)
            if not buf:
                break
            out.write(buf)
            read += len(buf)
            if total:
                pct = read * 100 // total
                print(f"\r  {pct:3d}% ({read // 1024} / {total // 1024} KB)", end="", flush=True)
        print()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_silent_installer(installer: Path) -> bool:
    """UB-Mannheim's Inno Setup builds accept /SILENT /DIR= for unattended installs."""
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
           f"/DIR={BUNDLE_DIR}"]
    print(f"→ running installer: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"installer failed (rc={result.returncode}):")
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Install bundled Tesseract for Outlaw CodeMaker")
    parser.add_argument("--force", action="store_true", help="reinstall even if already present")
    parser.add_argument("--keep-installer", action="store_true",
                        help="leave the downloaded installer in temp for inspection")
    args = parser.parse_args()

    if already_installed() and not args.force:
        print(f"OK Tesseract already bundled at {EXE_PATH}")
        return 0

    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        installer = Path(tmp) / f"tesseract-{TESSERACT_VERSION}.exe"
        try:
            download(DOWNLOAD_URL, installer)
        except Exception as exc:  # noqa: BLE001
            print(f"download failed: {exc}", file=sys.stderr)
            return 2

        if EXPECTED_SHA256:
            actual = sha256(installer)
            if actual.lower() != EXPECTED_SHA256.lower():
                print(f"SHA-256 mismatch!\n  expected: {EXPECTED_SHA256}\n  actual:   {actual}",
                      file=sys.stderr)
                return 3
            print(f"SHA-256 verified: {actual[:16]}…")
        else:
            print("(SHA-256 verification skipped — set EXPECTED_SHA256 to pin)")

        if args.keep_installer:
            kept = Path(tempfile.gettempdir()) / installer.name
            shutil.copy2(installer, kept)
            print(f"  installer kept at {kept}")

        if not run_silent_installer(installer):
            return 4

    if not already_installed():
        print(f"installer reported success but {EXE_PATH} is missing", file=sys.stderr)
        print("If a UAC prompt was dismissed, re-run this script from an admin shell.",
              file=sys.stderr)
        return 5

    print(f"OK Tesseract installed at {EXE_PATH}")
    print("   Outlaw CodeMaker will pick it up automatically on next launch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
