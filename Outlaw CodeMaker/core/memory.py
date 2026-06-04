"""Working memory — the agent's in-RAM view of the current session.

Wraps MemoryStore (SQLite) for fast access during a single conversation, and
flushes back to the DB as messages and traces accumulate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from db.schema import MemoryStore

from .api_client import ChatMessage


@dataclass
class SessionContext:
    session_id: int
    title: str
    workspace_root: str
    chat: list[ChatMessage] = field(default_factory=list)

    def append(self, role: str, content: str) -> None:
        self.chat.append(ChatMessage(role=role, content=content))

    def as_history(self, max_messages: int = 20) -> list[ChatMessage]:
        """Trimmed chat history for prompt context."""
        return self.chat[-max_messages:]


class MemoryManager:
    def __init__(self, store: MemoryStore):
        self.store = store
        self.current: SessionContext | None = None

    def start_session(self, title: str, workspace_root: str) -> SessionContext:
        session_id = self.store.create_session(title=title, workspace=workspace_root)
        self.current = SessionContext(
            session_id=session_id,
            title=title,
            workspace_root=workspace_root,
        )
        return self.current

    def resume_session(self, session_id: int, title: str, workspace_root: str) -> SessionContext:
        self.current = SessionContext(
            session_id=session_id,
            title=title,
            workspace_root=workspace_root,
        )
        for msg in self.store.get_messages(session_id):
            self.current.chat.append(ChatMessage(role=msg.role, content=msg.content))
        return self.current

    def record_user(self, content: str) -> int:
        assert self.current is not None
        is_first = len(self.current.chat) == 0
        self.current.append("user", content)
        msg_id = self.store.add_message(self.current.session_id, "user", content)
        # Auto-title a fresh session from its first user message.
        if is_first and self.current.title in {"New session", "", None}:
            title = self._derive_title(content)
            self.current.title = title
            self.store.rename_session(self.current.session_id, title)
        return msg_id

    @staticmethod
    def _derive_title(text: str, limit: int = 48) -> str:
        clean = " ".join(text.strip().split())
        if len(clean) <= limit:
            return clean or "Untitled"
        return clean[:limit].rstrip() + "…"

    def record_assistant(self, content: str, metadata: dict | None = None) -> int:
        assert self.current is not None
        self.current.append("assistant", content)
        return self.store.add_message(self.current.session_id, "assistant", content, metadata)

    def record_tool(self, tool_name: str, result: str) -> int:
        assert self.current is not None
        self.current.append("tool", f"[{tool_name}] {result}")
        return self.store.add_message(
            self.current.session_id,
            "tool",
            result,
            metadata={"tool": tool_name},
        )

    def record_trace(self, message_id: int, stage: str, body: str) -> None:
        self.store.save_reasoning_trace(message_id, stage, body)
