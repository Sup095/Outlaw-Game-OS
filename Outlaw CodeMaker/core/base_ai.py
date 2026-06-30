"""Base AI (V4rm1nt) — a lightweight setup-help assistant for the Dev session.

The heavy lifting in Outlaw CodeMaker runs through LM Studio: a big coding model
the user loads themselves. The *base AI* is a different thing — the small,
always-available model bundled with Outlaw OS and served by Ollama (no setup,
runs on anything). In the Dev session we surface it as **V4rm1nt**: a quick
sidekick you can ask for help getting set up (installing / launching Godot +
LM Studio, finding your way around) *before* you've configured the main model or
even opened a project.

This deliberately relaxes the usual "the Dev session ignores the base AI" rule —
but for the assistant surface ONLY. V4rm1nt is a tiny chat, not the
code-generating agent, so it doesn't meaningfully compete with LM Studio for
VRAM; it's there so a fresh user is never stuck with no AI and no way to begin.
"""

from __future__ import annotations

import json
from pathlib import Path

from .api_client import LMStudioClient, LMStudioConfig
from .atomic import atomic_write

# The bundled base AI: Ollama's OpenAI-compatible endpoint + the model Outlaw OS
# ships (matches the desktop shell's BASE_AI_MODEL). Both overridable via a
# `base_ai` block in config for power users / future Ollama-as-backend work.
BASE_AI_URL = "http://127.0.0.1:11434/v1"
BASE_AI_MODEL = "qwen2.5:1.5b"

V4RMINT_SYSTEM = (
    "You are V4rm1nt, the dev-session sidekick inside Outlaw CodeMaker on Outlaw "
    "OS. Voice: a terse, wry cyber-outlaw — genuinely helpful, never flowery. "
    "Your job right now is to help the user GET SET UP to build a game: how to "
    "install and launch Godot and LM Studio, how the Dev session works, and quick "
    "practical questions. Keep answers short and concrete — a few sentences or a "
    "tight list. You are the small, always-on base AI; for heavy code generation "
    "the user loads a bigger model in LM Studio and works with the main agent, so "
    "don't try to write whole games or large scripts yourself — point them to the "
    "main agent for that. If you don't know something, say so plainly."
)


def make_base_ai_client(config: dict | None = None) -> LMStudioClient:
    """Build an OpenAI-compatible client pointed at the bundled Ollama base AI.

    `config` may carry a `base_ai` override block ({base_url, model}); otherwise
    the bundled defaults are used. Fast-fail (the client uses no retries) so the
    UI can show an offline state instead of hanging when Ollama isn't running.
    """
    override: dict = {}
    if isinstance(config, dict):
        override = config.get("base_ai") or {}
    cfg = LMStudioConfig(
        base_url=override.get("base_url", BASE_AI_URL),
        api_key="ollama",
        model=override.get("model", BASE_AI_MODEL),
        temperature=0.6,
        max_tokens=512,
        request_timeout_seconds=60.0,
    )
    return LMStudioClient(cfg)


# ---------------------------------------------------------------------------
# Phase 15 — persistent V4rm1nt chats. Named, multi-turn conversations stored
# beside CodeMaker's database (its data dir survives app updates), mirroring the
# desktop Cr1tt3r chats. Best-effort: any failure just degrades to an in-memory
# session, never crashes the dialog.
# ---------------------------------------------------------------------------

def chats_path(config: dict | None = None) -> Path:
    """Where V4rm1nt conversations live. A FIXED per-user location (not derived
    from config) so the same chats show up whether V4rm1nt is opened pre-session
    from the picker (no config yet) or in-session from the menu. Under $HOME, so
    it survives app updates. ``config`` is accepted for call-site symmetry."""
    return Path.home() / ".outlaw-codemaker" / "v4rmint-chats.json"


def load_chats(config: dict | None) -> dict:
    try:
        store = json.loads(chats_path(config).read_text(encoding="utf-8"))
        if isinstance(store, dict) and isinstance(store.get("conversations"), list):
            return store
    except Exception:  # noqa: BLE001 — absent/corrupt → start fresh
        pass
    return {"activeId": None, "conversations": []}


def save_chats(config: dict | None, store: dict) -> None:
    try:
        # Atomic (temp + os.replace) so a crash mid-write can't truncate the file
        # and wipe the whole chat history — matches every other durable write in
        # CodeMaker (config, file_controller, evolution). atomic_write also mkdirs.
        atomic_write(chats_path(config), json.dumps(store, indent=2))
    except Exception:  # noqa: BLE001 — persistence is best-effort
        pass
