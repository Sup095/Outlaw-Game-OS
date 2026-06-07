"""Disk-backed context cache (Phase 6).

The orchestrator rebuilds expensive context every turn — the workspace tree,
RAG retrievals, solution recall, project roadmaps/summaries. Recomputing them
costs time, and re-stuffing them into the model's context window costs tokens
(which on a tight GPU costs VRAM, and in low-VRAM "spill" mode costs system
RAM). This module lets the agent compute such context ONCE and reuse it from
disk across turns and across sessions — so the AI keeps its working memory
without paying for it in RAM/VRAM each turn.

Design goals:
  * Pure standard library (json, hashlib, os, time) — no new dependencies.
  * Per-project namespacing so two games never collide.
  * Content-addressed keys: the caller hands us the *inputs* a value depends
    on (e.g. workspace path + tree depth + a directory-mtime signature) and we
    hash them, so a stale value is simply never found once an input changes.
  * TTL per entry + a total-size cap with oldest-first eviction, so the cache
    can never grow without bound on a small disk.
  * Corruption-tolerant: a damaged entry reads as a miss, never an exception.

It is deliberately tiny and synchronous; entries are small JSON blobs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# A schema tag baked into every entry. Bump it if the on-disk format changes,
# so old entries are transparently ignored rather than mis-read.
_SCHEMA = 1


def signature(*parts: Any) -> str:
    """Build a stable cache key from the inputs a value depends on.

    Pass everything that should invalidate the value when it changes — paths,
    sizes, a directory mtime, a query string, the VRAM mode, etc. Order
    matters; equal inputs in the same order produce the same key.
    """
    h = hashlib.sha256()
    for p in parts:
        h.update(repr(p).encode("utf-8", "replace"))
        h.update(b"\x1f")  # unit separator so "ab"+"c" != "a"+"bc"
    return h.hexdigest()[:32]


class ContextCache:
    """A small, safe, per-namespace JSON cache on disk.

    Parameters
    ----------
    root_dir:
        Base directory for all cache data. Created if missing.
    namespace:
        Logical bucket (typically the project / workspace). Sanitised into a
        subdirectory so different projects don't share entries.
    max_bytes:
        Soft cap on total bytes for this namespace. When exceeded after a
        write, the oldest entries are evicted until back under the cap.
    """

    def __init__(self, root_dir: str | os.PathLike, namespace: str = "default",
                 max_bytes: int = 32 * 1024 * 1024):
        self.root = Path(root_dir)
        self.namespace = self._safe_ns(namespace)
        self.dir = self.root / self.namespace
        self.max_bytes = int(max_bytes)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("context cache dir unavailable (%s) — caching disabled", e)

    # -- key/path helpers ----------------------------------------------------
    @staticmethod
    def _safe_ns(ns: str) -> str:
        ns = (ns or "default").strip()
        # Hash anything path-unsafe to keep one flat, predictable dir name.
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in ns)[:48]
        return f"{safe}_{hashlib.sha256(ns.encode()).hexdigest()[:8]}" if ns else "default"

    def _path(self, key: str) -> Path:
        # key may be a raw signature() output or arbitrary text — hash either way.
        digest = key if (len(key) == 32 and all(c in "0123456789abcdef" for c in key)) \
            else hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:32]
        return self.dir / f"{digest}.json"

    # -- core API ------------------------------------------------------------
    def get(self, key: str, ttl: Optional[float] = None) -> Optional[Any]:
        """Return the cached value for ``key`` or ``None`` on miss/expiry.

        If ``ttl`` (seconds) is given, an entry older than that is treated as a
        miss (and removed). A missing/corrupt entry is a clean miss.
        """
        p = self._path(key)
        try:
            raw = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        try:
            entry = json.loads(raw)
            if not isinstance(entry, dict) or entry.get("schema") != _SCHEMA:
                return None
        except (json.JSONDecodeError, ValueError):
            # Corrupt — discard it so it doesn't keep failing.
            self._safe_unlink(p)
            return None
        if ttl is not None:
            ts = entry.get("ts", 0)
            if (time.time() - ts) > ttl:
                self._safe_unlink(p)
                return None
        # Refresh access time so eviction is roughly LRU, not just oldest-write.
        try:
            os.utime(p, None)
        except OSError:
            pass
        return entry.get("value")

    def set(self, key: str, value: Any) -> bool:
        """Store ``value`` (must be JSON-serialisable). Returns True on success."""
        p = self._path(key)
        entry = {"schema": _SCHEMA, "ts": time.time(), "value": value}
        try:
            data = json.dumps(entry, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logger.debug("context cache: value for %r not serialisable: %s", key, e)
            return False
        # Atomic-ish write: temp then replace, so a reader never sees a partial file.
        tmp = p.with_suffix(".json.tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, p)
        except OSError as e:
            logger.debug("context cache write failed: %s", e)
            self._safe_unlink(tmp)
            return False
        self._enforce_cap()
        return True

    def get_or_compute(self, key: str, compute: Callable[[], Any],
                       ttl: Optional[float] = None) -> Any:
        """Return the cached value, or compute + store + return it on a miss.

        ``compute`` is only called on a miss. If it raises, the exception
        propagates (we don't cache failures).
        """
        hit = self.get(key, ttl=ttl)
        if hit is not None:
            return hit
        value = compute()
        if value is not None:
            self.set(key, value)
        return value

    def invalidate(self, key: str) -> None:
        self._safe_unlink(self._path(key))

    def clear(self) -> int:
        """Remove every entry in this namespace. Returns count removed."""
        n = 0
        for f in self._entry_files():
            if self._safe_unlink(f):
                n += 1
        return n

    def stats(self) -> dict:
        files = self._entry_files()
        total = 0
        for f in files:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return {"entries": len(files), "bytes": total, "max_bytes": self.max_bytes,
                "namespace": self.namespace, "dir": str(self.dir)}

    # -- internals -----------------------------------------------------------
    def _entry_files(self) -> list[Path]:
        try:
            return [f for f in self.dir.glob("*.json") if f.is_file()]
        except OSError:
            return []

    def _enforce_cap(self) -> None:
        files = self._entry_files()
        try:
            sized = [(f, f.stat().st_size, f.stat().st_mtime) for f in files]
        except OSError:
            return
        total = sum(s for _, s, _ in sized)
        if total <= self.max_bytes:
            return
        # Evict oldest-accessed first until under cap.
        for f, size, _ in sorted(sized, key=lambda t: t[2]):
            if total <= self.max_bytes:
                break
            if self._safe_unlink(f):
                total -= size

    @staticmethod
    def _safe_unlink(p: Path) -> bool:
        try:
            p.unlink()
            return True
        except OSError:
            return False
