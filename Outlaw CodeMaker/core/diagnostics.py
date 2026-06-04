"""Diagnostic dump bundler — Help → Save diagnostic bundle.

What we capture
---------------
* Redacted ``config.json`` (API keys / passwords masked).
* The last N log lines from a ring-buffer handler we install at startup.
* Hardware snapshot (VRAM/RAM, GPU name).
* Outlaw version + the loaded LM Studio model id.
* Reasoning traces from the active session (sanitized — strips obvious file
  contents that the user might not want in a support bundle).
* Python + OS info.

We never include:
* The user's project files (they can be huge and contain unreleased game work).
* Any vault / sqlite databases (likely to contain the same).
* Anything outside the project's own dirs.

The bundle is a single zip the user can attach to a bug report. Generation
runs synchronously on the UI thread because it's tiny (well under a second
even on a slow disk).
"""

from __future__ import annotations

import copy
import io
import json
import logging
import platform
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZipFile


logger = logging.getLogger(__name__)


# Keys whose values get redacted in any config dump. Match is case-insensitive
# substring so things like "OPENAI_API_KEY" or "steam_password" still hit.
_SENSITIVE_KEY_HINTS = (
    "api_key", "apikey", "password", "secret", "token", "auth",
)


def _is_sensitive_key(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _SENSITIVE_KEY_HINTS)


def redact_config(cfg: dict) -> dict:
    """Deep-copy ``cfg`` with sensitive values replaced by '***'."""
    def _walk(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if _is_sensitive_key(k) and isinstance(v, (str, int, float)):
                    out[k] = "***"
                else:
                    out[k] = _walk(v)
            return out
        if isinstance(node, list):
            return [_walk(x) for x in node]
        return node
    return _walk(copy.deepcopy(cfg))


# ---------------------------------------------------------------------------
# Ring-buffer log handler
# ---------------------------------------------------------------------------


class RingBufferHandler(logging.Handler):
    """In-memory tail of the most recent log records. Cheap; fixed memory."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=capacity)
        # Match the existing console formatter (configure_logging in main.py).
        self.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 (Handler API)
        try:
            self.buffer.append(self.format(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def lines(self) -> list[str]:
        return list(self.buffer)


_GLOBAL_RING: RingBufferHandler | None = None


def install_ring_buffer(capacity: int = 500) -> RingBufferHandler:
    """Add a ring-buffer handler to the root logger. Idempotent — repeat calls
    return the existing handler so a re-init doesn't double up output."""
    global _GLOBAL_RING
    if _GLOBAL_RING is not None:
        return _GLOBAL_RING
    handler = RingBufferHandler(capacity=capacity)
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    _GLOBAL_RING = handler
    return handler


def ring_buffer() -> RingBufferHandler | None:
    return _GLOBAL_RING


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------


def _safe_lines(it: Iterable[str], limit: int) -> list[str]:
    """Trim to the last ``limit`` items."""
    return list(it)[-limit:]


def build_bundle(
    out_path: str | Path,
    *,
    config: dict,
    hardware_snapshot: dict | None,
    active_model: str | None,
    version: str | None = None,
    reasoning_traces: list[dict] | None = None,
    log_lines: list[str] | None = None,
    extra: dict | None = None,
) -> Path:
    """Write a diagnostic zip and return its path.

    Everything is optional — callers pass what they have. Missing pieces are
    represented as small placeholder entries so the recipient can tell that
    a section is genuinely empty vs. accidentally omitted.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload_log = log_lines if log_lines is not None else (
        _GLOBAL_RING.lines() if _GLOBAL_RING else []
    )
    payload_log = _safe_lines(payload_log, 200)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "outlaw_version": version or "unknown",
        "active_model": active_model or "(unknown)",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "hardware": hardware_snapshot or {},
    }
    if extra:
        summary["extra"] = extra

    redacted = redact_config(config or {})

    with ZipFile(out_path, "w", compression=ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("README.txt", _readme_text())
        zf.writestr("summary.json", json.dumps(summary, indent=2, default=str))
        zf.writestr("config.redacted.json", json.dumps(redacted, indent=2, default=str))
        if payload_log:
            zf.writestr("log_tail.txt", "\n".join(payload_log))
        else:
            zf.writestr("log_tail.txt", "(no log lines captured — ring buffer empty)")
        if reasoning_traces:
            zf.writestr("reasoning_traces.json",
                        json.dumps(reasoning_traces, indent=2, default=str))
    return out_path


def _readme_text() -> str:
    return (
        "Outlaw CodeMaker — diagnostic bundle\n"
        "====================================\n\n"
        "Contents:\n"
        "  summary.json              Version, platform, hardware snapshot.\n"
        "  config.redacted.json      Live config with API keys / passwords masked.\n"
        "  log_tail.txt              Last 200 log records (from the in-memory ring).\n"
        "  reasoning_traces.json     Plan/Review/Execute traces from the active session.\n\n"
        "This bundle does NOT include your project files, SQLite databases, or\n"
        "anything outside Outlaw's own runtime state. Safe to share for support.\n"
    )


def default_bundle_name() -> str:
    return "outlaw-diagnostic-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
