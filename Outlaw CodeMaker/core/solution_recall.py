"""Solution recall — "you've solved this before" lookups against the
:mod:`core.evolution` log.

The evolution module already records every successful self-upgrade (a
problem-and-fix pair, with a validation pass and a list of files that
changed). On a low-VRAM box, the cheapest way to make the agent smarter is
to hand the model the relevant lessons it learned in past sessions instead
of rediscovering them from scratch.

This module wires that up using the same hashing vectoriser the Vault uses
(CPU-only, zero VRAM cost). Given a new task, we score every successful
evolution by cosine similarity over (rationale + detail), keep the best few,
and hand the orchestrator a compact prompt block to inject before planning.

Why this lives outside ``core.evolution``: the evolution engine is mostly
about *producing* fixes. Recall is a lightweight read-side concern shared
by the orchestrator (planning) and potentially the Steam panel /
roadmap-builder in the future. Keeping it separate keeps the dependency
arrows clean.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from db.schema import MemoryStore
from db.vault import vectorize


logger = logging.getLogger(__name__)


# Bare-minimum similarity for a recall to be considered relevant. Below this
# we're injecting noise — the model is better off without it.
DEFAULT_MIN_SCORE = 0.18


@dataclass(frozen=True)
class RecalledSolution:
    rationale: str
    detail: str
    files_changed: list[str]
    score: float
    created_at: str

    def to_prompt_line(self, max_chars: int = 240) -> str:
        # Compact one-bullet line — the prompt block can hold several of these
        # without ballooning the context.
        text = (self.rationale or self.detail or "").strip().replace("\n", " ")
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        files = ""
        if self.files_changed:
            shown = ", ".join(self.files_changed[:3])
            files = f"  ({shown}{'…' if len(self.files_changed) > 3 else ''})"
        return f"- {text}{files}"


class SolutionRecall:
    """Finds past fixes whose write-ups overlap with a new task.

    Lazy on purpose — vectors are computed on demand at search time, not stored.
    The evolution log is small (curated, swapped-only entries), so a fresh scan
    per task is fine: ~200 entries × 1024-dim dot product ≈ negligible CPU.
    """

    def __init__(
        self,
        store: MemoryStore,
        min_score: float = DEFAULT_MIN_SCORE,
        max_scan: int = 200,
    ):
        self.store = store
        self.min_score = min_score
        self.max_scan = max_scan

    def recall(self, query: str, top_k: int = 3) -> list[RecalledSolution]:
        """Return up to ``top_k`` past solutions sorted by similarity."""
        if not query or not query.strip() or top_k <= 0:
            return []
        try:
            entries = self.store.list_evolution(limit=self.max_scan)
        except Exception:  # noqa: BLE001
            logger.exception("Could not list evolution entries for recall.")
            return []

        # Only successful, applied (non-dry-run) entries are worth recalling —
        # those represent real fixes that survived validation.
        useful = [
            e for e in entries
            if e.get("validation") == "PASS"
            and int(e.get("swapped") or 0) == 1
            and int(e.get("dry_run") or 0) == 0
        ]
        if not useful:
            return []

        qvec = vectorize(query)
        scored: list[tuple[float, dict]] = []
        for e in useful:
            text = ((e.get("rationale") or "") + " " + (e.get("detail") or "")).strip()
            if not text:
                continue
            evec = vectorize(text)
            score = float(np.dot(qvec, evec))  # unit vectors → cosine
            if score >= self.min_score:
                scored.append((score, e))

        scored.sort(key=lambda x: -x[0])
        results: list[RecalledSolution] = []
        for score, e in scored[:top_k]:
            files = e.get("files_changed") or []
            if isinstance(files, str):  # safety: DB sometimes stores stringified JSON
                files = []
            results.append(RecalledSolution(
                rationale=(e.get("rationale") or "").strip(),
                detail=(e.get("detail") or "").strip(),
                files_changed=files,
                score=round(score, 3),
                created_at=e.get("created_at", ""),
            ))
        return results

    @staticmethod
    def render_prompt_block(solutions: list[RecalledSolution]) -> str:
        """Build the compact block the orchestrator pastes into ``workspace_summary``.

        Empty string when there are no useful recalls — keeps the prompt clean
        instead of teaching the model that "no past solutions" is its own state.
        """
        if not solutions:
            return ""
        lines = ["## Past solutions (from previous self-upgrades — may be relevant):"]
        for s in solutions:
            lines.append(s.to_prompt_line())
        return "\n".join(lines)
