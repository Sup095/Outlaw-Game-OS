"""VRAM-saver — auto-shrinks every context input when VRAM is tight.

Two-layer architecture (see SC7 in the System Core roadmap)
-----------------------------------------------------------

**Layer 1 — Always-on free savings (no perf cost, on by default in every mode).**
These are inherent properties of how the rest of Outlaw CodeMaker is built;
this module does not reach into them, it just relies on them being true:

    * Heavy modules (``cv2``, ``mss``, ``numpy``, ``openai``) are imported
      lazily, behind first use — Python's standard "import inside the function
      that needs it" pattern in :mod:`vision.vision_engine`, :mod:`vision.godot_bridge`,
      :mod:`core.snapshots`, etc. They never load if the user never opens those code paths.
    * The Vault (RAG index) is CPU-only — see :mod:`db.vault`. The model is
      already large in VRAM; the index never goes there.
    * No model preloading. We let LM Studio own model lifecycle; we never
      "warm up" a load.
    * The active model id from ``/v1/models`` is cached per-session, not
      re-queried per request — see :class:`LMStudioClient` in :mod:`core.api_client`.
    * Every poller pauses when its widget is hidden / its screen is not visible.

These free savings cost nothing and stay on regardless of which mode the user
picks below. They are documented here as a reminder; this module does not
implement them.

**Layer 2 — Conditional emergency mode (this module).**
Only kicks in when (a) free VRAM falls under the LEAN/MINIMAL thresholds in
``auto`` mode, or (b) the user manually pins ``lean`` / ``minimal`` in
Settings. Effects shrink every model input per turn:

    * workspace tree at shallower depth + fewer files
    * fewer RAG hits, shorter slices each
    * smaller history slice
    * tighter ``max_tokens``

That trades wall-clock for survival — the agent loop runs more iterations with
smaller chunks, and the user's still building their game. Pairs with the
existing :class:`HardwareMonitor` (NVML-backed; non-NVIDIA hosts get the FULL
default since we can't measure free VRAM there).

Three tiers:

    FULL     — plenty of headroom; behave normally.
    LEAN     — under ~1.5 GB free; halve everything reasonable.
    MINIMAL  — under ~600 MB free or LM Studio just OOM'd; bare-minimum context,
               accept that turns will be short and the agent will need more
               iterations to get anywhere.

Mode source can be ``auto`` (read VRAM), ``off`` (always FULL), or one of the
mode names directly (force LEAN/MINIMAL regardless of hardware).

The shell-side System Core (in ``outlaw-shell/vram-tier.js``) uses the SAME
thresholds so both halves of Outlaw OS classify identically on the same
hardware. If you tune the thresholds below, mirror them in the JS file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .hardware import HardwareMonitor


logger = logging.getLogger(__name__)


class VRAMMode(Enum):
    FULL = "full"
    LEAN = "lean"
    MINIMAL = "minimal"

    @property
    def label(self) -> str:
        return {"full": "Full", "lean": "Lean", "minimal": "Minimal"}[self.value]


@dataclass(frozen=True)
class ContextBudget:
    """Per-turn limits the orchestrator should respect. Smaller = less VRAM
    pressure but more iterations needed to do real work."""

    workspace_tree_depth: int
    workspace_tree_max_lines: int
    rag_top_k: int
    rag_max_chars: int
    history_max_messages: int
    max_output_tokens: int
    solution_recall_k: int

    @classmethod
    def for_mode(cls, mode: VRAMMode, base_max_tokens: int) -> "ContextBudget":
        if mode is VRAMMode.MINIMAL:
            return cls(
                workspace_tree_depth=1,
                workspace_tree_max_lines=30,
                rag_top_k=1,
                rag_max_chars=400,
                history_max_messages=4,
                max_output_tokens=min(512, base_max_tokens),
                solution_recall_k=1,
            )
        if mode is VRAMMode.LEAN:
            return cls(
                workspace_tree_depth=2,
                workspace_tree_max_lines=80,
                rag_top_k=2,
                rag_max_chars=1000,
                history_max_messages=8,
                max_output_tokens=min(1024, base_max_tokens),
                solution_recall_k=2,
            )
        # FULL — keep CodeMaker's pre-existing defaults.
        return cls(
            workspace_tree_depth=3,
            workspace_tree_max_lines=10_000,
            rag_top_k=3,
            rag_max_chars=1800,
            history_max_messages=12,
            max_output_tokens=base_max_tokens,
            solution_recall_k=3,
        )


@dataclass(frozen=True)
class SpillPlan:
    """How to run the model on THIS machine when VRAM is the constraint.

    On a tight GPU we keep only some of the model's layers in VRAM and "spill"
    the rest into system RAM (run on the CPU) — much slower, but it lets a
    bigger / better model run at all. This plan captures the recommendation so
    the orchestrator can act on it and the UI can explain it.
    """

    gpu_offload: float          # 0.0–1.0 fraction of layers to keep on the GPU
    prefer_cpu: bool            # mostly CPU/RAM (extreme low-VRAM)
    aggressive_cache: bool      # lean hard on the disk context cache
    model_hint: str             # human guidance on model size to pick
    reason: str

    def to_dict(self) -> dict:
        return {
            "gpu_offload": self.gpu_offload,
            "prefer_cpu": self.prefer_cpu,
            "aggressive_cache": self.aggressive_cache,
            "model_hint": self.model_hint,
            "reason": self.reason,
        }


def compute_spill_plan(mode: "VRAMMode", snapshot: dict) -> SpillPlan:
    """Recommend GPU offload + model sizing from the mode + a hardware snapshot.

    Pure function (no hardware access) so it's easy to test. ``snapshot`` is a
    :meth:`HardwareMonitor.snapshot` dict; missing keys are tolerated.
    """
    free = snapshot.get("vram_free_mb")
    ram_total = snapshot.get("ram_total_mb")

    if mode is VRAMMode.FULL:
        return SpillPlan(
            gpu_offload=1.0, prefer_cpu=False, aggressive_cache=False,
            model_hint="Any model that fits your VRAM — full GPU acceleration.",
            reason="Plenty of VRAM headroom; keep the whole model on the GPU.",
        )

    if mode is VRAMMode.LEAN:
        return SpillPlan(
            gpu_offload=0.5, prefer_cpu=False, aggressive_cache=True,
            model_hint="A ~7B model at Q4 (~4–5 GB) keeps most layers on the GPU.",
            reason="VRAM is moderate; keep ~half the layers on the GPU and "
                   "lean on the disk cache to keep context out of memory.",
        )

    # MINIMAL — extreme low VRAM. Spill most/all of the model into system RAM.
    if free is None:
        offload = 0.0
    elif free < 600:
        offload = 0.0
    elif free < 1500:
        offload = 0.2
    else:
        offload = 0.35
    ram_note = ""
    if isinstance(ram_total, (int, float)) and ram_total and ram_total < 8192:
        ram_note = " (Heads up: with under 8 GB RAM this will be quite slow — " \
                   "consider an even smaller model.)"
    return SpillPlan(
        gpu_offload=offload, prefer_cpu=True, aggressive_cache=True,
        model_hint="A small model (~3B at Q4, ~2–3 GB) or smaller. Most layers "
                   "run on the CPU using system RAM — slower, but it works." + ram_note,
        reason="Very little free VRAM; spill the model into system RAM and "
               "rely on the cached roadmap/context so we don't re-stuff memory each turn.",
    )


# Modes accepted from config / Settings UI. "auto" reads VRAM; the mode names
# force-pin (handy for benchmarking or for users who know their hardware).
_VALID_PREFS = {"auto", "off", "full", "lean", "minimal"}


class VRAMSaver:
    """Resolves a user preference + live hardware into a :class:`VRAMMode`."""

    # Free-VRAM (MiB) thresholds for the auto-mode classifier. Picked to match
    # the Hardware.suggest_max_tokens ladder so the two don't fight each other.
    _MINIMAL_BELOW_MB = 600
    _LEAN_BELOW_MB = 1500
    # C5 — when there's no measurable VRAM (no NVIDIA/NVML, an iGPU, or a RAM-only
    # box), protect system RAM instead. Thresholds = available system RAM (MiB).
    _MINIMAL_RAM_BELOW_MB = 1024
    _LEAN_RAM_BELOW_MB = 2560

    def __init__(self, hardware: HardwareMonitor, mode_pref: str = "auto"):
        self.hardware = hardware
        self._pref = self._normalize(mode_pref)
        self._last_mode = self.current_mode()

    @staticmethod
    def _normalize(pref: str) -> str:
        pref = (pref or "auto").lower().strip()
        if pref not in _VALID_PREFS:
            logger.warning("Unknown vram_saver mode %r — defaulting to 'auto'.", pref)
            return "auto"
        return pref

    def set_pref(self, pref: str) -> None:
        self._pref = self._normalize(pref)

    @property
    def pref(self) -> str:
        return self._pref

    def current_mode(self) -> VRAMMode:
        """Pick the active mode, accounting for the user's preference + VRAM."""
        if self._pref == "off" or self._pref == "full":
            return VRAMMode.FULL
        if self._pref == "lean":
            return VRAMMode.LEAN
        if self._pref == "minimal":
            return VRAMMode.MINIMAL
        # "auto" — derive from free VRAM.
        snap = self.hardware.snapshot()
        free = snap.get("vram_free_mb")
        if free is None:
            # C5 — no measurable VRAM (no NVIDIA/NVML, an iGPU, or a RAM-only box).
            # Instead of assuming endless headroom, protect system RAM: shrink the
            # per-turn context when AVAILABLE RAM gets tight.
            ram_total = snap.get("ram_total_mb") or 0
            ram_used = snap.get("ram_used_mb") or 0
            ram_avail = ram_total - ram_used
            if ram_total:
                if ram_avail < self._MINIMAL_RAM_BELOW_MB:
                    return VRAMMode.MINIMAL
                if ram_avail < self._LEAN_RAM_BELOW_MB:
                    return VRAMMode.LEAN
            return VRAMMode.FULL
        if free < self._MINIMAL_BELOW_MB:
            return VRAMMode.MINIMAL
        if free < self._LEAN_BELOW_MB:
            return VRAMMode.LEAN
        return VRAMMode.FULL

    def budget(self, base_max_tokens: int) -> ContextBudget:
        return ContextBudget.for_mode(self.current_mode(), base_max_tokens)

    def spill_plan(self) -> SpillPlan:
        """Recommend GPU offload + model sizing for the current mode + VRAM."""
        return compute_spill_plan(self.current_mode(), self.hardware.snapshot())

    def mode_changed_since_last_check(self) -> bool:
        """True if the auto-classifier flipped the mode since the last call.

        Lets the UI emit a single notification on a transition (e.g., "VRAM is
        getting tight — switched to LEAN") instead of spamming on every tick.
        """
        cur = self.current_mode()
        if cur != self._last_mode:
            self._last_mode = cur
            return True
        return False


# ---------------------------------------------------------------------------
# Helpers shared by the orchestrator when applying a budget
# ---------------------------------------------------------------------------


def truncate_tree(tree_text: str, max_lines: int) -> str:
    """Cap a directory-tree dump to ``max_lines``, adding a marker if cut."""
    if max_lines <= 0:
        return ""
    lines = tree_text.splitlines()
    if len(lines) <= max_lines:
        return tree_text
    kept = lines[:max_lines]
    cut = len(lines) - max_lines
    kept.append(f"... ({cut} more entries trimmed for VRAM saver)")
    return "\n".join(kept)


def slice_messages(messages: Iterable, max_messages: int) -> list:
    """Keep only the most recent ``max_messages`` items, preserving order."""
    msgs = list(messages)
    if max_messages <= 0:
        return []
    return msgs[-max_messages:] if len(msgs) > max_messages else msgs
