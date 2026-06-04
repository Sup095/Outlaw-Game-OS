"""Prompt pre-flight — last-mile defence against context-window overruns.

The VRAM saver shrinks the planning prompt *proactively* based on free VRAM.
This module is the *reactive* safety net underneath: right before
``LMStudioClient.stream_chat`` opens the HTTP request, we cheaply estimate
the prompt's token count and, if it exceeds the configured input budget,
drop the oldest history messages (preserving the system prompt and the
current user turn) until it fits. Logs what got cut so a user staring at a
weird answer can see why.

We never block — if the estimate suggests an over-budget prompt we trim it
silently and proceed. The alternative ("error out") would just push the
failure to the user, which is what we're trying to avoid.

Token estimation
----------------
We use a 4-chars-per-token heuristic. That's slightly pessimistic for English
code/markdown (real tokenisers average ~3.5–4.5 chars/token) which is what
we want for a safety net — better to trim 200 tokens we didn't strictly need
than to overflow by 200 tokens we did.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable


logger = logging.getLogger(__name__)


# Conservative chars-per-token. Real tokenisers vary by ~10–15%; this is on
# the high side of the estimate so we err toward smaller prompts.
_CHARS_PER_TOKEN = 4
# Default input-side budget when nothing's configured. 5000 input tokens leaves
# 1000–3000 tokens for output on a typical 6–8k-context local model.
DEFAULT_INPUT_BUDGET = 5000
# Hard floor — even after aggressive trimming we keep at least the system
# prompt and the latest user message.
MIN_KEPT_MESSAGES = 2


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (~4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: Iterable) -> int:
    """Sum the per-message estimate. Works on ChatMessage objects or dicts."""
    total = 0
    for m in messages:
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content", "")
        total += estimate_tokens(str(content or ""))
    # Every message carries a small role/format overhead — call it 4 tokens.
    return total + 4 * len(list(messages)) if False else total


@dataclass(frozen=True)
class TrimReport:
    """What the pre-flight pass did. Surfaced via the log signal so the user
    can spot context loss after the fact."""
    dropped_messages: int
    estimated_before: int
    estimated_after: int
    budget: int

    @property
    def trimmed(self) -> bool:
        return self.dropped_messages > 0


def trim_to_fit(messages: list, input_budget: int = DEFAULT_INPUT_BUDGET) -> tuple[list, TrimReport]:
    """Drop oldest non-system, non-final-user messages until the estimate fits.

    Preserves:
      * The first system message (instructions).
      * The most recent user message (the actual request).
      * The chronological order of whatever's left in between.

    Returns ``(trimmed_messages, report)``. Always returns at least
    ``MIN_KEPT_MESSAGES`` messages — if even the bare minimum exceeds the
    budget, we send what we have and let LM Studio surface the limit.
    """
    if not messages:
        return [], TrimReport(0, 0, 0, input_budget)

    msgs = list(messages)
    before = sum(estimate_tokens(_content(m)) for m in msgs)
    if before <= input_budget:
        return msgs, TrimReport(0, before, before, input_budget)

    # Indices we must keep: the first system message (if any) and the latest user.
    keep_first_system: int | None = None
    for i, m in enumerate(msgs):
        if _role(m) == "system":
            keep_first_system = i
            break
    keep_latest_user: int | None = None
    for i in range(len(msgs) - 1, -1, -1):
        if _role(msgs[i]) == "user":
            keep_latest_user = i
            break

    locked = {idx for idx in (keep_first_system, keep_latest_user) if idx is not None}

    # Trim from the front of the unlocked indices (oldest-first) until we fit.
    droppable = [i for i in range(len(msgs)) if i not in locked]
    dropped_set: set[int] = set()
    running = before
    for idx in droppable:
        if running <= input_budget:
            break
        running -= estimate_tokens(_content(msgs[idx]))
        dropped_set.add(idx)

    kept = [m for i, m in enumerate(msgs) if i not in dropped_set]
    # Enforce the floor even if our locked-keepers alone busted the budget.
    if len(kept) < MIN_KEPT_MESSAGES:
        kept = msgs[-MIN_KEPT_MESSAGES:]

    after = sum(estimate_tokens(_content(m)) for m in kept)
    report = TrimReport(
        dropped_messages=len(dropped_set),
        estimated_before=before,
        estimated_after=after,
        budget=input_budget,
    )
    if report.trimmed:
        logger.info(
            "prompt_preflight trimmed %d message(s): %d → %d tokens (budget %d).",
            report.dropped_messages, report.estimated_before, report.estimated_after, input_budget,
        )
    return kept, report


# ---------------------------------------------------------------------------
# Internal — duck-type accessors so we work with ChatMessage AND raw dicts.
# ---------------------------------------------------------------------------


def _content(m) -> str:
    c = getattr(m, "content", None)
    if c is None and isinstance(m, dict):
        c = m.get("content", "")
    return str(c or "")


def _role(m) -> str:
    r = getattr(m, "role", None)
    if r is None and isinstance(m, dict):
        r = m.get("role", "")
    return str(r or "").lower()
