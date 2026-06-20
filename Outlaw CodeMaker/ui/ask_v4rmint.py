"""Ask V4rm1nt — a small base-AI chat for setup help in the Dev session.

Reachable from the project picker (the pre-session launcher) so a fresh user can
get help installing / launching Godot + LM Studio before configuring anything or
opening a project. Backed by the bundled Ollama base AI (see core.base_ai) and
streamed off the UI thread so the window never freezes. Fails soft: if the base
AI isn't running, it says so and the user can still use the setup buttons.
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.api_client import ChatMessage
from core.base_ai import V4RMINT_SYSTEM, make_base_ai_client

from .styles import COLORS


class _ChatWorker(QThread):
    """Streams one base-AI reply. Lives only for the duration of a turn."""

    token = pyqtSignal(str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, client, messages):
        super().__init__()
        self._client = client
        self._messages = messages

    def run(self):  # noqa: D401 — QThread entry point
        try:
            full = ""
            for ev in self._client.stream_chat(self._messages, temperature=0.6, max_tokens=512):
                if ev.error:
                    self.failed.emit(ev.error)
                    return
                if ev.delta:
                    self.token.emit(ev.delta)
                if ev.done:
                    full = ev.full_text
            self.done.emit(full)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class AskV4rmintDialog(QDialog):
    """A minimal multi-turn chat with the bundled base AI, themed to match."""

    def __init__(self, config: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ask V4rm1nt — setup help")
        self.resize(560, 540)

        self._client = make_base_ai_client(config)
        self._history = [ChatMessage(role="system", content=V4RMINT_SYSTEM)]
        self._worker: _ChatWorker | None = None
        self._cur_reply = ""

        title = QLabel("V4rm1nt")
        title.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 14pt; font-weight: 700; letter-spacing: 1px;"
        )
        sub = QLabel("Your dev-session sidekick — ask about setting up Godot or LM Studio.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {COLORS['muted']}; font-size: 9pt;")

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            f"QTextEdit {{ background: {COLORS['code_bg']}; color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 8px;"
            f" font-size: 10.5pt; }}"
        )

        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask V4rm1nt…  (e.g. how do I set up LM Studio?)")
        self.input.setStyleSheet(
            f"QLineEdit {{ background: {COLORS['panel']}; color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 7px 9px; }}"
            f"QLineEdit:focus {{ border-color: {COLORS['accent']}; }}"
        )
        self.input.returnPressed.connect(self._on_send)

        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self._on_send)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        input_row = QHBoxLayout()
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_btn)
        input_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addSpacing(6)
        layout.addWidget(self.transcript, 1)
        layout.addLayout(input_row)

        # Instant greeting — no model call, so the window is useful immediately
        # even while the base AI is still warming up (or absent).
        self._add_line("V4rm1nt", "Need a hand getting set up? Ask me about installing or "
                       "launching Godot or LM Studio, or how the Dev session works.")
        self.input.setFocus()

    # ------------------------------------------------------------------
    # Transcript helpers
    # ------------------------------------------------------------------

    def _add_line(self, who: str, text: str) -> None:
        color = COLORS["accent"] if who == "V4rm1nt" else COLORS["accent2"]
        self.transcript.append(f'<span style="color:{color}; font-weight:700;">{who}:</span> '
                               f'<span style="color:{COLORS["text"]};">{_esc(text)}</span>')

    def _begin_reply(self) -> None:
        self._cur_reply = ""
        self.transcript.append(f'<span style="color:{COLORS["accent"]}; font-weight:700;">V4rm1nt:</span> ')

    def _append_token(self, tok: str) -> None:
        self._cur_reply += tok
        cur = self.transcript.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.transcript.setTextCursor(cur)
        self.transcript.insertPlainText(tok)
        self.transcript.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Send / stream
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self.input.setEnabled(not busy)
        self.send_btn.setEnabled(not busy)
        self.send_btn.setText("…" if busy else "Send")
        if not busy:
            self.input.setFocus()

    def _on_send(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._add_line("You", text)
        self._history.append(ChatMessage(role="user", content=text))
        self._begin_reply()
        self._set_busy(True)

        self._worker = _ChatWorker(self._client, list(self._history))
        self._worker.token.connect(self._append_token)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, full: str) -> None:
        reply = (full or self._cur_reply).strip()
        if reply:
            self._history.append(ChatMessage(role="assistant", content=reply))
        self.transcript.append("")  # spacer line
        self._set_busy(False)

    def _on_failed(self, err: str) -> None:
        self.transcript.append(
            f'<span style="color:{COLORS["warn"]};">V4rm1nt is offline — the bundled base AI '
            f"may not be running yet. You can still set up Godot and LM Studio from the buttons.</span>"
        )
        self._set_busy(False)

    # ------------------------------------------------------------------

    def closeEvent(self, e) -> None:
        w = self._worker
        if w is not None and w.isRunning():
            # Detach so a late signal can't touch this dying dialog, then let the
            # short reply finish (tiny model + 512-token cap → quick).
            try:
                w.token.disconnect()
                w.done.disconnect()
                w.failed.disconnect()
            except (TypeError, RuntimeError):
                pass
            w.wait(3000)
        super().closeEvent(e)


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
