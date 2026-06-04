"""Approval gate — pauses an agent file write until the user approves the diff.

The agent runs on a worker thread; the diff dialog must run on the UI thread.
`request()` (called from the worker) emits a signal carrying a "ticket" and then
*blocks the worker* on a threading.Event until the UI thread calls `resolve()`.

When the gate is disabled, writes auto-approve (full autonomy mode).
On shutdown, `cancel_all()` releases any blocked worker as a denial so the app
can exit cleanly.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, pyqtSignal


class ApprovalGate(QObject):
    # (path, old_text, new_text, kind, ticket-dict)
    review_requested = pyqtSignal(str, str, str, str, object)

    def __init__(self, enabled: bool = True):
        super().__init__()
        self.enabled = enabled
        self._pending: set[threading.Event] = set()
        self._lock = threading.Lock()

    def request(self, path: str, old_text: str, new_text: str, kind: str = "write") -> bool:
        """Block the calling (worker) thread until the user decides. Returns approved?"""
        if not self.enabled:
            return True
        if old_text == new_text:
            return True  # nothing actually changes

        event = threading.Event()
        ticket = {"event": event, "approved": False}
        with self._lock:
            self._pending.add(event)
        self.review_requested.emit(path, old_text, new_text, kind, ticket)

        # Wait up to 5 minutes; timeout denies (safer than hanging).
        got = event.wait(timeout=300)
        with self._lock:
            self._pending.discard(event)
        return bool(ticket["approved"]) if got else False

    def resolve(self, ticket: dict, approved: bool) -> None:
        """Called on the UI thread after the dialog closes."""
        ticket["approved"] = approved
        ticket["event"].set()

    def cancel_all(self) -> None:
        with self._lock:
            for event in list(self._pending):
                event.set()
            self._pending.clear()


class QuestionGate(QObject):
    """Lets the agent ask the user a question mid-task and block until answered.

    Same pause-the-worker pattern as ApprovalGate: `ask()` runs on the worker
    thread, emits a signal carrying the question spec + a ticket, and blocks on a
    threading.Event until the UI thread calls `resolve()` with the answer.
    """

    question_requested = pyqtSignal(dict, object)        # (spec, ticket)
    batch_question_requested = pyqtSignal(list, object)  # (specs, ticket)

    def __init__(self):
        super().__init__()
        self._pending: set[threading.Event] = set()
        self._lock = threading.Lock()

    def ask(self, spec: dict) -> str:
        event = threading.Event()
        ticket = {"event": event, "answer": ""}
        with self._lock:
            self._pending.add(event)
        self.question_requested.emit(spec, ticket)
        got = event.wait(timeout=900)  # 15 min — the user is reading/thinking
        with self._lock:
            self._pending.discard(event)
        if not got:
            return "(no answer — proceed with sensible defaults)"
        return ticket["answer"] or "(skipped)"

    def ask_batch(self, specs: list[dict]) -> list[str]:
        """Ask several questions in ONE prompt; returns one answer per question."""
        if not specs:
            return []
        event = threading.Event()
        ticket = {"event": event, "answers": []}
        with self._lock:
            self._pending.add(event)
        self.batch_question_requested.emit(specs, ticket)
        got = event.wait(timeout=1800)  # 30 min for a whole batch
        with self._lock:
            self._pending.discard(event)
        if not got or not ticket["answers"]:
            return ["(no answer — use sensible defaults)"] * len(specs)
        return ticket["answers"]

    def resolve(self, ticket: dict, answer: str) -> None:
        ticket["answer"] = answer
        ticket["event"].set()

    def resolve_batch(self, ticket: dict, answers: list[str]) -> None:
        ticket["answers"] = answers
        ticket["event"].set()

    def cancel_all(self) -> None:
        with self._lock:
            for event in list(self._pending):
                event.set()
            self._pending.clear()
