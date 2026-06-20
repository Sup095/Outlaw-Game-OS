"""Ask V4rm1nt — a small base-AI chat for setup help in the Dev session.

Reachable from the project picker (the pre-session launcher) so a fresh user can
get help installing / launching Godot + LM Studio before configuring anything or
opening a project. Backed by the bundled Ollama base AI (see core.base_ai) and
streamed off the UI thread so the window never freezes. Fails soft: if the base
AI isn't running, it says so and the user can still use the setup buttons.
"""

from __future__ import annotations

import time
import uuid

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.api_client import ChatMessage
from core.base_ai import V4RMINT_SYSTEM, load_chats, make_base_ai_client, save_chats

from .styles import COLORS

# Recent turns re-sent to the small base model each ask (older turns stay saved).
V4RMINT_WINDOW = 12


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
        self._config = config
        self._store = load_chats(config)     # {activeId, conversations:[{id,title,messages}]}
        self._ensure_active()
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

        # Phase 15 — conversation bar: switch / new / delete persistent chats.
        self.chat_combo = QComboBox()
        self.chat_combo.setStyleSheet(
            f"QComboBox {{ background: {COLORS['panel']}; color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 4px 8px; }}"
        )
        self.chat_combo.currentIndexChanged.connect(self._on_switch)
        self.new_btn = QPushButton("+ New")
        self.new_btn.clicked.connect(self._new_chat)
        self.del_btn = QPushButton("Delete")
        self.del_btn.clicked.connect(self._delete_chat)
        chat_row = QHBoxLayout()
        chat_row.addWidget(self.chat_combo, 1)
        chat_row.addWidget(self.new_btn)
        chat_row.addWidget(self.del_btn)

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
        layout.addLayout(chat_row)
        layout.addSpacing(6)
        layout.addWidget(self.transcript, 1)
        layout.addLayout(input_row)

        # Populate the chat list and show the active (persisted) conversation.
        self._rebuild_combo()
        self._load_active()
        self.input.setFocus()

    # ------------------------------------------------------------------
    # Conversations (persistent — stored beside the DB, survive updates)
    # ------------------------------------------------------------------

    def _new_convo(self, title: str = "New chat") -> dict:
        return {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "auto_titled": True,
            "created_at": time.time(),
            "updated_at": time.time(),
            "messages": [],   # [{role, content}] — the system prompt isn't stored
        }

    def _ensure_active(self) -> None:
        convos = self._store.setdefault("conversations", [])
        if not convos:
            c = self._new_convo("Chat 1")
            convos.append(c)
            self._store["activeId"] = c["id"]
        if not any(c["id"] == self._store.get("activeId") for c in convos):
            self._store["activeId"] = convos[0]["id"]

    def _active(self) -> dict | None:
        for c in self._store.get("conversations", []):
            if c["id"] == self._store.get("activeId"):
                return c
        return None

    def _persist(self) -> None:
        save_chats(self._config, self._store)

    def _rebuild_combo(self) -> None:
        self.chat_combo.blockSignals(True)
        self.chat_combo.clear()
        for c in self._store.get("conversations", []):
            self.chat_combo.addItem(c.get("title") or "Untitled", c["id"])
        idx = self.chat_combo.findData(self._store.get("activeId"))
        if idx >= 0:
            self.chat_combo.setCurrentIndex(idx)
        self.chat_combo.blockSignals(False)

    def _load_active(self) -> None:
        """Rebuild the transcript + the model history from the active convo."""
        self.transcript.clear()
        self._history = [ChatMessage(role="system", content=V4RMINT_SYSTEM)]
        c = self._active()
        if not c or not c.get("messages"):
            self._add_line("V4rm1nt", "Need a hand getting set up? Ask me about installing or "
                           "launching Godot or LM Studio, or how the Dev session works.")
            return
        for m in c["messages"]:
            who = "You" if m.get("role") == "user" else "V4rm1nt"
            self._add_line(who, m.get("content", ""))
            self._history.append(ChatMessage(role=m.get("role", "user"), content=m.get("content", "")))

    def _record(self, role: str, content: str) -> None:
        c = self._active()
        if not c or not content:
            return
        c.setdefault("messages", []).append({"role": role, "content": content})
        c["updated_at"] = time.time()
        # Auto-title from the first user message.
        if role == "user" and c.get("auto_titled") and \
                sum(1 for m in c["messages"] if m["role"] == "user") == 1:
            c["title"] = (content[:40] + "…") if len(content) > 40 else content
            self._rebuild_combo()
        self._persist()

    def _on_switch(self, _idx: int) -> None:
        cid = self.chat_combo.currentData()
        if not cid or cid == self._store.get("activeId"):
            return
        if self._worker is not None and self._worker.isRunning():
            return  # don't switch mid-reply
        self._store["activeId"] = cid
        self._persist()
        self._load_active()

    def _new_chat(self) -> None:
        c = self._new_convo("Chat %d" % (len(self._store.get("conversations", [])) + 1))
        self._store["conversations"].append(c)
        self._store["activeId"] = c["id"]
        self._persist()
        self._rebuild_combo()
        self._load_active()
        self.input.setFocus()

    def _delete_chat(self) -> None:
        c = self._active()
        if not c or (self._worker is not None and self._worker.isRunning()):
            return
        reply = QMessageBox.question(
            self, "Delete chat",
            f'Delete "{c.get("title") or "this chat"}"? This can\'t be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        convos = [x for x in self._store["conversations"] if x["id"] != c["id"]]
        if not convos:
            convos.append(self._new_convo("Chat 1"))
        self._store["conversations"] = convos
        self._store["activeId"] = convos[0]["id"]
        self._persist()
        self._rebuild_combo()
        self._load_active()

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
        self._record("user", text)
        self._begin_reply()
        self._set_busy(True)

        # Windowed memory: system prompt + the most recent turns (older turns stay
        # saved on disk but aren't re-sent, keeping the small base model fast).
        msgs = [self._history[0]] + self._history[1:][-V4RMINT_WINDOW:]
        self._worker = _ChatWorker(self._client, msgs)
        self._worker.token.connect(self._append_token)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, full: str) -> None:
        reply = (full or self._cur_reply).strip()
        if reply:
            self._history.append(ChatMessage(role="assistant", content=reply))
            self._record("assistant", reply)
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
