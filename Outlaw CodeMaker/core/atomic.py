"""Atomic file writes — never leave a half-written .py behind.

Write to `<path>.tmp` then `os.replace()` onto the target. os.replace is atomic
on Windows and POSIX, so a power loss or crash mid-write leaves either the old
file intact or the new file complete — never a truncated mix. This is what makes
the Phoenix hot-swap safe to interrupt.
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding=encoding, newline="") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)  # atomic


def atomic_copy(src: str | Path, dst: str | Path) -> None:
    src, dst = Path(src), Path(dst)
    atomic_write(dst, src.read_text(encoding="utf-8", errors="replace"))
