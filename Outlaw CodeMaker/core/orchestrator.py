"""The Orchestrator — Outlaw CodeMaker's agentic loop.

Flow:
    user_task
       │
       ▼
   build context (workspace summary + history)
       │
       ▼
   ChainOfThoughtReasoner.reason() — streams Plan → Review → Execute
       │   (each stage emits a stage_chunk signal for the Thought Chamber)
       ▼
   parse <<TOOL>>...<<END>> blocks from the Execute stage
       │
       ▼
   dispatch tools via ToolRegistry; capture results
       │
       ▼
   if tools fired → feed results back, loop up to max_tool_iterations
   else            → emit <<ANSWER>> block as the final assistant message

The whole thing runs in a QThread to keep the UI responsive. The class is a
QObject so signals can fan out to Thought Chamber, terminal log, and chat view.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from db.schema import MemoryStore

from .api_client import ChatMessage, LMStudioClient, LMStudioConfig
from .memory import MemoryManager
from .reasoning import ChainOfThoughtReasoner, ReasoningTrace
from .snapshots import SnapshotManager
from .solution_recall import SolutionRecall
from .vram_saver import VRAMSaver, truncate_tree
from .context_cache import ContextCache, signature
from . import lmstudio_tuning


logger = logging.getLogger(__name__)


TOOL_PATTERN = re.compile(r"<<TOOL>>(.*?)<<END>>", re.DOTALL)
ANSWER_PATTERN = re.compile(r"<<ANSWER>>(.*?)<<END>>", re.DOTALL)
ASK_PATTERN = re.compile(r"<<ASK>>(.*?)<<END>>", re.DOTALL)
ROADMAP_PATTERN = re.compile(r"<<ROADMAP>>(.*?)<<END>>", re.DOTALL)
DESCRIPTION_PATTERN = re.compile(r"<<DESCRIPTION>>(.*?)<<END>>", re.DOTALL)  # Phase 14b: game design sheet
CONTINUE_PATTERN = re.compile(r"<<CONTINUE>>", re.IGNORECASE)
# Verbatim file writes — content needs NO JSON escaping (reliable for local models).
WRITE_PATTERN = re.compile(
    r'<<WRITE\s+(?:file|path)\s*=\s*["\']([^"\']+)["\']\s*>>\n?(.*?)<<END>>',
    re.DOTALL | re.IGNORECASE,
)
APPEND_PATTERN = re.compile(
    r'<<APPEND\s+(?:file|path)\s*=\s*["\']([^"\']+)["\']\s*>>\n?(.*?)<<END>>',
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class ToolCall:
    name: str
    args: dict


def parse_tool_calls(text: str) -> tuple[list["ToolCall"], list[str]]:
    """Pure parser: returns (valid tool calls, raw strings that failed to parse).

    Kept free of any Qt/self dependency so it is trivially unit-testable.
    """
    calls: list[ToolCall] = []
    errors: list[str] = []
    for raw in TOOL_PATTERN.findall(text or ""):
        raw = raw.strip()
        try:
            payload = json.loads(raw)
            name = payload.get("name")
            args = payload.get("args", {}) or {}
            if name:
                calls.append(ToolCall(name=name, args=args))
        except json.JSONDecodeError:
            errors.append(raw)
    return calls, errors


def parse_answer(text: str) -> str | None:
    m = ANSWER_PATTERN.search(text or "")
    return m.group(1).strip() if m else None


THINK_PATTERN = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    """Remove Qwen3-style <think>…</think> reasoning blocks before parsing markers.

    The orchestrator already does explicit Plan→Review→Execute, so a model's own
    internal reasoning is noise for tool/answer extraction. Handles an unclosed
    block (truncated stream) by stripping to end.
    """
    return THINK_PATTERN.sub("", text or "")


@dataclass
class Control:
    """Everything the Execute stage asked the orchestrator to do this turn."""
    tools: list["ToolCall"]
    asks: list[dict]
    roadmap: dict | None
    answer: str | None
    wants_continue: bool
    tool_errors: list[str]  # raw <<TOOL>> blocks that failed to parse (malformed JSON)
    description: dict | None = None  # Phase 14b: AI-authored/updated game design sheet


def parse_control(text: str) -> Control:
    """Parse all agent control markers from an Execute output (think-stripped)."""
    text = strip_think(text or "")
    tools, tool_errors = parse_tool_calls(text)

    # Verbatim <<WRITE>> / <<APPEND>> blocks → synthetic file tool calls.
    # This is the RELIABLE way to write code: no JSON escaping of multiline content.
    for mw in WRITE_PATTERN.finditer(text):
        tools.append(ToolCall("file_write", {"path": mw.group(1).strip(), "content": mw.group(2)}))
    for ma in APPEND_PATTERN.finditer(text):
        tools.append(ToolCall("file_append", {"path": ma.group(1).strip(), "content": ma.group(2)}))

    asks: list[dict] = []
    for raw in ASK_PATTERN.findall(text):
        try:
            spec = json.loads(raw.strip())
            if isinstance(spec, dict) and spec.get("question"):
                asks.append(spec)
        except json.JSONDecodeError:
            pass

    roadmap = None
    m = ROADMAP_PATTERN.search(text)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, dict):
                roadmap = data
        except json.JSONDecodeError:
            pass

    description = None
    m = DESCRIPTION_PATTERN.search(text)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, dict):
                description = data
        except json.JSONDecodeError:
            pass

    return Control(
        tools=tools,
        asks=asks,
        roadmap=roadmap,
        answer=parse_answer(text),
        wants_continue=bool(CONTINUE_PATTERN.search(text)),
        tool_errors=tool_errors,
        description=description,
    )


@dataclass
class ToolResult:
    name: str
    ok: bool
    payload: str


class ToolRegistry:
    """Tools are plain callables: fn(args: dict) -> str. Result string is fed back to the model."""

    def __init__(self):
        self._tools: dict[str, callable] = {}

    def register(self, name: str, fn) -> None:
        self._tools[name] = fn

    def has(self, name: str) -> bool:
        return name in self._tools

    def describe(self) -> str:
        return "\n".join(f"- {name}" for name in sorted(self._tools))

    def call(self, call: ToolCall) -> ToolResult:
        if call.name not in self._tools:
            return ToolResult(call.name, False, f"unknown tool '{call.name}'")
        try:
            out = self._tools[call.name](call.args or {})
            return ToolResult(call.name, True, str(out))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Tool '%s' failed", call.name)
            return ToolResult(call.name, False, f"ERROR: {exc}")


class _AgentWorker(QThread):
    """Background runner for a single user task."""

    stage_chunk = pyqtSignal(str, str)        # (stage, delta_text)
    stage_done = pyqtSignal(str, str)         # (stage, full_text)
    tool_invoked = pyqtSignal(str, dict)      # (tool_name, args)
    tool_result = pyqtSignal(str, bool, str)  # (tool_name, ok, payload)
    final_answer = pyqtSignal(str)            # final user-visible message
    log = pyqtSignal(str, str)                # (level, text) for terminal log
    failed = pyqtSignal(str)                  # error string
    progress = pyqtSignal(int, str)           # (percent 0-100, phase label)
    roadmap_updated = pyqtSignal(dict)        # agent created/updated the roadmap
    description_updated = pyqtSignal(dict)     # agent created/updated the design sheet
    paused = pyqtSignal(str)                  # task paused but resumable (hint text)
    connection_lost = pyqtSignal(str)         # transient LM Studio error; reconnect mode on

    def __init__(
        self,
        user_task: str,
        reasoner: ChainOfThoughtReasoner,
        tools: ToolRegistry,
        memory: MemoryManager,
        workspace_summary: str,
        max_tool_iterations: int,
        question_gate=None,
        resume_state: dict | None = None,
        history_max_messages: int = 12,
    ):
        super().__init__()
        self.user_task = user_task
        self.reasoner = reasoner
        self.tools = tools
        self.memory = memory
        self.workspace_summary = workspace_summary
        self.max_tool_iterations = max_tool_iterations
        self.question_gate = question_gate
        self.resume_state = resume_state
        # The VRAM saver sets this — tighter slices when memory is tight.
        self.history_max_messages = history_max_messages
        self._reviewed_plan = ""
        self._observations: list[str] = []
        # Set by _stream_reasoning / _stream_execute_only when the stage exited
        # because of a transient error. The run loop reads it to decide whether
        # to save the checkpoint as "paused" (reconnect-recoverable) or
        # "failed" (a hard error the user has to address).
        self._last_error_was_transient = False

    def run(self) -> None:
        try:
            self._run_agent_loop()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent worker crashed")
            # Checkpoint so the user can 'continue' after a mid-task failure.
            self._save_checkpoint(status="failed")
            self.failed.emit(str(exc))

    def _save_checkpoint(self, status: str = "paused") -> None:
        if self.memory.current:
            try:
                self.memory.store.save_task_state(
                    self.memory.current.session_id, self.user_task,
                    self._reviewed_plan, self._observations, status,
                )
            except Exception:  # noqa: BLE001
                pass

    def _clear_checkpoint(self) -> None:
        if self.memory.current:
            try:
                self.memory.store.clear_task_state(self.memory.current.session_id)
            except Exception:  # noqa: BLE001
                pass

    def _save_roadmap(self, data: dict) -> None:
        try:
            ws = self.memory.current.workspace_root if self.memory.current else ""
            self.memory.store.save_roadmap(ws, data)
            self.roadmap_updated.emit(data)
            self.log.emit("ok", "Roadmap updated.")
        except Exception as exc:  # noqa: BLE001
            self.log.emit("warn", f"Roadmap save failed: {exc}")

    def _save_description(self, data: dict) -> None:
        try:
            ws = self.memory.current.workspace_root if self.memory.current else ""
            self.memory.store.save_description(ws, data)
            self.description_updated.emit(data)
            self.log.emit("ok", "Design sheet updated.")
        except Exception as exc:  # noqa: BLE001
            self.log.emit("warn", f"Design sheet save failed: {exc}")

    def _run_agent_loop(self) -> None:
        resuming = self.resume_state is not None
        # On resume, log a short "continue" rather than re-logging the whole task.
        user_msg_id = self.memory.record_user("continue" if resuming else self.user_task)
        history = (
            self.memory.current.as_history(max_messages=self.history_max_messages)
            if self.memory.current else []
        )

        if resuming:
            self.log.emit("info", "Continuing the previous task where it left off…")
            self._reviewed_plan = self.resume_state.get("reviewed_plan") or ""
            self._observations = list(self.resume_state.get("observations") or [])
        else:
            self.log.emit("info", f"Agent received task: {self.user_task[:80]}")
            self._reviewed_plan = ""
            self._observations = []

        for iteration in range(self.max_tool_iterations):
            if self.isInterruptionRequested():
                self.log.emit("warn", "Interrupted — stopping agent loop.")
                self._save_checkpoint()
                return

            first_pass = iteration == 0 and not resuming
            if first_pass:
                self.progress.emit(5, "Planning…")
                trace = self._stream_reasoning(self.user_task, history)
                if trace is None:  # stream errored (e.g. server/VRAM) — make it resumable
                    # Connection-drop errors are recoverable: save as paused so
                    # the orchestrator's auto-continue path can pick it back
                    # up when LM Studio comes back online.
                    status = "paused" if self._last_error_was_transient else "failed"
                    self._save_checkpoint(status=status)
                    return
                self._persist_trace(user_msg_id, trace)
                self._reviewed_plan = trace.review or trace.plan
                execute_text = trace.execute
            else:
                label = "Continuing…" if (resuming and iteration == 0) else f"Iteration {iteration + 1}"
                self.progress.emit(70, label)
                self.stage_chunk.emit("execute", f"\n\n── {label} ──\n")
                execute_text = self._stream_execute_only(
                    self._reviewed_plan, self._observations, history
                )
                if execute_text is None:  # stream errored — checkpoint so Continue works
                    status = "paused" if self._last_error_was_transient else "failed"
                    self._save_checkpoint(status=status)
                    return
                self.memory.record_trace(user_msg_id, "execute", execute_text)

            ctrl = parse_control(execute_text)

            if ctrl.roadmap:
                self._save_roadmap(ctrl.roadmap)
            if ctrl.description:
                self._save_description(ctrl.description)

            acted = False

            # Questions — show them ALL in one prompt, then continue automatically.
            if ctrl.asks:
                acted = True
                self.progress.emit(40, f"Waiting for your answers ({len(ctrl.asks)})…")
                self.log.emit("info", f"❓ asking {len(ctrl.asks)} question(s)")
                answers = self.question_gate.ask_batch(ctrl.asks) if self.question_gate else []
                for spec, ans in zip(ctrl.asks, answers):
                    self.memory.record_tool("user_answer", f"Q: {spec.get('question')}\nA: {ans}")
                    self._observations.append(f"[you answered] Q: {spec.get('question')}\nA: {ans}")

            # Tools
            if ctrl.tools:
                acted = True
                n = len(ctrl.tools)
                self.progress.emit(88, f"Running {n} tool(s)…")
                for i, tc in enumerate(ctrl.tools, start=1):
                    if self.isInterruptionRequested():
                        self._save_checkpoint()
                        return
                    self.tool_invoked.emit(tc.name, tc.args)
                    self.log.emit("info", f"→ tool {tc.name}({_short(tc.args)})")
                    result = self.tools.call(tc)
                    self.tool_result.emit(result.name, result.ok, result.payload)
                    self.log.emit("info" if result.ok else "error",
                                  f"← {tc.name}: {result.payload[:200]}")
                    self.memory.record_tool(result.name, result.payload)
                    self._observations.append(f"[{result.name} ok={result.ok}]\n{result.payload}")
                    self.progress.emit(88 + int(7 * i / n), f"Tool {i}/{n}: {tc.name}")

            if acted:
                self._save_checkpoint()

            # Final answer this turn?
            if ctrl.answer is not None:
                self.progress.emit(100, "Done")
                self._emit_answer(ctrl.answer)
                if ctrl.wants_continue:
                    self._save_checkpoint(status="paused")
                    self.paused.emit("There's more to do — click ▸ Continue or type 'continue'.")
                else:
                    self._clear_checkpoint()
                return

            if acted:
                continue  # loop again with new observations / answers

            # A tool call was MALFORMED (e.g. multiline code crammed into JSON) and
            # nothing else happened. Don't give up — coach the model to use the
            # verbatim <<WRITE>> block and retry instead of "stopping at write".
            if ctrl.tool_errors:
                self.log.emit("warn", "Malformed tool call — asking the model to use <<WRITE>>.")
                self._observations.append(
                    "[system] Your last tool call could not be parsed (multiline code does not "
                    "fit in one-line JSON). To create or edit a file, use this VERBATIM block "
                    "instead — no JSON escaping needed:\n"
                    '<<WRITE file="scripts/Player.gd">>\n'
                    "<the full file content exactly as it should appear>\n"
                    "<<END>>\n"
                    "Re-emit the file now using <<WRITE>>."
                )
                self._save_checkpoint()
                continue

            # Nothing actionable parsed → treat the text as the answer.
            self.progress.emit(100, "Done")
            self._emit_answer(execute_text or "(no response)")
            self._clear_checkpoint()
            return

        # Ran out of steps — pause as resumable instead of failing.
        self._save_checkpoint(status="paused")
        self.paused.emit(
            f"Reached the {self.max_tool_iterations}-step limit for one turn. "
            "Click ▸ Continue or type 'continue' to keep going."
        )

    def _stream_execute_only(
        self, reviewed_plan: str, observations: list[str], history: list[ChatMessage]
    ) -> str | None:
        gen = self.reasoner.execute_only(
            user_request=self.user_task,
            reviewed_plan=reviewed_plan,
            workspace_summary=self.workspace_summary,
            observations=observations,
            history=history,
        )
        try:
            while True:
                if self.isInterruptionRequested():
                    gen.close()
                    return None
                ev = next(gen)
                if ev.error:
                    # Transient = LM Studio went away mid-stream. Save a
                    # checkpoint and tell the orchestrator to poll for it
                    # instead of dropping the task on the floor.
                    if getattr(ev, "transient", False):
                        self._last_error_was_transient = True
                        self.connection_lost.emit(f"[execute] {ev.error}")
                        return None
                    self.failed.emit(f"[execute] {ev.error}")
                    return None
                if ev.done:
                    self.stage_done.emit(ev.stage, ev.full_text)
                    self.progress.emit(92, "Finalizing…")
                else:
                    self.stage_chunk.emit(ev.stage, ev.delta)
        except StopIteration as stop:
            return stop.value or ""

    def _stream_reasoning(self, request_text: str, history: list[ChatMessage]) -> ReasoningTrace | None:
        gen = self.reasoner.reason(
            user_request=request_text,
            workspace_summary=self.workspace_summary,
            history=history,
        )
        trace: ReasoningTrace | None = None
        try:
            while True:
                if self.isInterruptionRequested():
                    gen.close()
                    return None
                ev = next(gen)
                if ev.error:
                    if getattr(ev, "transient", False):
                        self._last_error_was_transient = True
                        self.connection_lost.emit(f"[{ev.stage}] {ev.error}")
                        return None
                    self.failed.emit(f"[{ev.stage}] {ev.error}")
                    return None
                if ev.done:
                    self.stage_done.emit(ev.stage, ev.full_text)
                    nxt = {
                        "plan": (33, "Reviewing…"),
                        "review": (66, "Executing…"),
                        "execute": (85, "Acting…"),
                    }.get(ev.stage)
                    if nxt:
                        self.progress.emit(*nxt)
                else:
                    self.stage_chunk.emit(ev.stage, ev.delta)
        except StopIteration as stop:
            trace = stop.value
        return trace

    def _parse_tool_calls(self, text: str) -> list[ToolCall]:
        calls, errors = parse_tool_calls(strip_think(text))
        for raw in errors:
            self.log.emit("warn", f"Could not parse tool call: {raw[:120]}")
        return calls

    def _parse_answer(self, text: str) -> str | None:
        return parse_answer(strip_think(text))

    def _persist_trace(self, message_id: int, trace: ReasoningTrace) -> None:
        for stage, body in trace.all_stages():
            if body:
                self.memory.record_trace(message_id, stage, body)

    def _emit_answer(self, answer: str) -> None:
        self.memory.record_assistant(answer)
        self.final_answer.emit(answer)


class _ConnCheckWorker(QThread):
    """Probes LM Studio off the UI thread so a dead server never freezes the window."""

    result = pyqtSignal(bool, str)

    def __init__(self, client: LMStudioClient):
        super().__init__()
        self.client = client

    def run(self) -> None:
        ok, msg = self.client.health_check()
        self.result.emit(ok, msg)


class _AdvisorWorker(QThread):
    """Runs the proactive-advice query off the UI thread."""

    advice_ready = pyqtSignal(str)

    def __init__(self, client: LMStudioClient, prompt: str):
        super().__init__()
        self.client = client
        self.prompt = prompt

    def run(self) -> None:
        try:
            resp = self.client.complete(
                [ChatMessage(role="user", content=self.prompt)],
                temperature=0.6,
                max_tokens=120,
            ).strip()
        except Exception:  # noqa: BLE001
            resp = ""
        self.advice_ready.emit(resp)


class Orchestrator(QObject):
    """The public face of the agent. UI talks to this; it owns the worker thread."""

    stage_chunk = pyqtSignal(str, str)
    stage_done = pyqtSignal(str, str)
    tool_invoked = pyqtSignal(str, dict)
    tool_result = pyqtSignal(str, bool, str)
    final_answer = pyqtSignal(str)
    log = pyqtSignal(str, str)
    failed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    connection_changed = pyqtSignal(bool, str)  # (online, message)
    advice_ready = pyqtSignal(str)
    progress = pyqtSignal(int, str)             # (percent 0-100, phase label)
    session_changed = pyqtSignal(int, str)      # (session_id, title)
    godot_error = pyqtSignal(str, str)          # (source, error text)
    vault_reindexed = pyqtSignal(int, str)      # (chunk_count, message)
    review_requested = pyqtSignal(str, str, str, str, object)  # forwarded from ApprovalGate
    workspace_changed = pyqtSignal(str)         # new workspace root after reconfigure
    evolution_status = pyqtSignal(str)          # phase label for the REGENERATE HUD
    evolution_candidate = pyqtSignal(dict)      # validated build awaiting approval
    evolution_failed = pyqtSignal(str)
    evolution_done = pyqtSignal(bool)
    question_requested = pyqtSignal(dict, object)  # agent asks the user (forwarded from QuestionGate)
    batch_question_requested = pyqtSignal(list, object)  # several questions in one prompt
    roadmap_updated = pyqtSignal(dict)          # roadmap created/updated
    description_updated = pyqtSignal(dict)       # design sheet created/updated
    task_paused = pyqtSignal(str)               # task pausable/resumable (hint)
    resumable_changed = pyqtSignal(bool)        # whether the current session has a resume point
    vram_mode_changed = pyqtSignal(str)         # current VRAM saver mode label (Full/Lean/Minimal)
    connection_lost = pyqtSignal(str)           # LM Studio dropped during a task; reconnect polling
    connection_restored = pyqtSignal()          # LM Studio is back; auto-continue triggered

    # Phrases that mean "pick up where you left off".
    CONTINUE_PHRASES = {"continue", "keep going", "resume", "go on", "carry on", "continue please"}

    def __init__(self, config: dict, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config

        lm_cfg = LMStudioConfig.from_dict(config["lm_studio"])
        self.client = LMStudioClient(lm_cfg)
        self.reasoner = ChainOfThoughtReasoner(self.client)

        self.store = MemoryStore(config["database"]["path"])
        self.memory = MemoryManager(self.store)

        # Lets the agent ask the user mid-task (blocking handshake to the UI).
        from core.approval import QuestionGate
        self.questions = QuestionGate()
        self.questions.question_requested.connect(self.question_requested)
        self.questions.batch_question_requested.connect(self.batch_question_requested)

        # Diff-approval gate (writes pause for your review unless disabled).
        from core.approval import ApprovalGate
        self.gate = ApprovalGate(enabled=True)
        self.gate.review_requested.connect(self.review_requested)

        self.tools = ToolRegistry()
        self._wire_tools()

        # The Vault — CPU RAG index over the project.
        from db.vault import Vault
        self.vault = Vault(
            db_path=config["database"].get("vault_path", "db/vault.db"),
            workspace_root=config["agent"]["workspace_root"],
        )

        # Hardware awareness (VRAM/RAM) + Phoenix Protocol (self-upgrade).
        from pathlib import Path as _Path
        from core.hardware import HardwareMonitor
        from core.evolution import EvolutionEngine
        self._project_root = _Path(__file__).resolve().parent.parent
        self.config_path = self._project_root / "config.json"
        self.hardware = HardwareMonitor()
        self._base_max_tokens = lm_cfg.max_tokens
        self.evolution = EvolutionEngine(project_root=self._project_root, client=self.client)

        # VRAM saver — single source of truth for context budgets at each turn.
        # On non-NVIDIA hosts (auto mode + no NVML) this stays at FULL by design.
        vram_pref = (config.get("vram_saver") or {}).get("mode", "auto")
        self.vram_saver = VRAMSaver(self.hardware, mode_pref=vram_pref)
        # Whether to auto-apply LM Studio GPU offload on low-VRAM transitions,
        # and which (small) model to load if so. Off by default — advisory only.
        _vs = config.get("vram_saver") or {}
        self._spill_apply = bool(_vs.get("spill_apply", False))
        self._low_vram_model = (config.get("lm_studio") or {}).get("low_vram_model") or None
        # Phase 6: disk-backed context cache. In lean/minimal modes the agent
        # reuses cached recall/roadmap context from disk instead of re-deriving
        # and re-stuffing it each turn — keeping working memory out of RAM/VRAM.
        try:
            from pathlib import Path as _CachePath
            _cache_root = _CachePath(__file__).resolve().parent.parent / "db" / "context_cache"
            self.context_cache = ContextCache(
                _cache_root, namespace=str(config["agent"].get("workspace_root") or "global"))
        except Exception:  # noqa: BLE001 — caching is optional, never fatal
            self.context_cache = None
        # Cheap CPU-only recall over past successful self-upgrades; injects
        # "you've solved this before" hints into the planning prompt.
        self.solution_recall = SolutionRecall(self.store)

        # Resolve workspace root once, up front — it's needed by both the
        # snapshot manager and the default session below.
        workspace_root = config["agent"]["workspace_root"]
        # Snapshot manager — captures the OLD state of any file the agent is
        # about to touch into <project>/.outlaw/snapshots/, so a bad edit can
        # always be rolled back via the Snapshots tab.
        self.snapshots = SnapshotManager(workspace_root)

        # Default session — UI may swap to a resumed one later
        self.memory.start_session(title="New session", workspace_root=workspace_root)

        self._worker: _AgentWorker | None = None
        self._conn_worker: _ConnCheckWorker | None = None
        self._advisor: _AdvisorWorker | None = None
        self._vault_worker: QThread | None = None
        self._evo_worker = None
        self._error_watcher = None
        self.online: bool | None = None  # None = never checked yet
        self.self_heal_enabled = bool(config.get("vision", {}).get("self_heal_enabled", False))
        # Reconnect-after-transient-error state. _reconnect_pending = True
        # means a task is paused waiting for LM Studio to come back; the
        # connection probe loop watches for it and triggers continue_task().
        self._reconnect_pending: bool = False
        self._reconnect_timer = None  # QTimer; lazy-created on first drop

    def _wire_tools(self) -> None:
        # Late imports to avoid pulling Qt/OpenCV at module load
        from tools.file_controller import FileController
        from vision.vision_engine import VisionEngine
        from vision.godot_bridge import GodotBridge
        from tools.automation import AutomationTool
        from vision.signal_mapper import SignalMapper

        workspace_root = self.config["agent"]["workspace_root"]

        self.file_controller = FileController(workspace_root)
        self.vision = VisionEngine(self.config["vision"])
        self.godot = GodotBridge(self.config["vision"], self.vision)
        self.automation = AutomationTool()
        self.signal_mapper = SignalMapper(workspace_root)

        # File ops — writes/deletes pass through the approval gate.
        self.tools.register("list_workspace", lambda a: self.file_controller.list_tree(a.get("depth", 4)))
        self.tools.register("file_read",      lambda a: self.file_controller.read(a["path"]))
        self.tools.register("read_lines",     lambda a: self.file_controller.read_lines(a["path"], a["start"], a["end"]))
        self.tools.register("file_write",     lambda a: self._guarded_write(a["path"], a["content"]))
        self.tools.register("file_append",    lambda a: self._guarded_append(a["path"], a["content"]))
        self.tools.register("file_delete",    lambda a: self._guarded_delete(a["path"]))
        self.tools.register("file_move",      lambda a: self._guarded_move(a["src"], a["dst"]))
        self.tools.register("file_mkdir",     lambda a: self.file_controller.mkdir(a["path"]))
        self.tools.register("file_search",    lambda a: self.file_controller.search(a["pattern"]))
        self.tools.register("grep",           lambda a: self.file_controller.search_text(a["query"], a.get("regex", False)))
        self.tools.register("search_text",    lambda a: self.file_controller.search_text(a["query"], a.get("regex", False)))

        # The Vault — RAG search over the indexed project.
        self.tools.register("vault_search",   lambda a: self._vault_search_tool(a["query"], a.get("top_k", 5)))

        # Godot project understanding + scene editing (writes gated).
        self.tools.register("godot_project_info", lambda a: self._godot_project_info())
        self.tools.register("godot_new_project",  lambda a: self._godot_new_project(a.get("name", "My Game")))
        self.tools.register("tscn_read",      lambda a: self._tscn_read(a["path"]))
        self.tools.register("tscn_create",    lambda a: self._tscn_create(a))
        self.tools.register("tscn_add_node",  lambda a: self._tscn_add_node(a))
        self.tools.register("tscn_connect",   lambda a: self._tscn_connect(a))
        self.tools.register("scan_signals",   lambda a: self.signal_mapper.scan().summary())

        # Vision / Godot
        self.tools.register("screenshot_godot", lambda a: self.godot.capture(a.get("save_path")))
        self.tools.register("ocr_godot",        lambda a: self.godot.ocr_text())
        self.tools.register("godot_focus",      lambda a: self.godot.focus_window())
        self.tools.register("active_window",    lambda a: self.godot.active_window_title())

        # Input automation
        self.tools.register("click",     lambda a: self.automation.click(a["x"], a["y"]))
        self.tools.register("type_text", lambda a: self.automation.type_text(a["text"]))
        self.tools.register("hotkey",    lambda a: self.automation.hotkey(*a["keys"]))

    # ----- gated write helpers (used by tools, run on the worker thread) ----

    def _current_session_id(self) -> int:
        return self.memory.current.session_id if self.memory.current else 0

    def _snapshot(self, rel_path: str, old_content: str, kind: str) -> None:
        """Capture the OLD state of a file we're about to change. Best-effort —
        we never let a snapshot failure block the agent's write."""
        try:
            self.snapshots.capture(rel_path, old_content, kind, self._current_session_id())
        except Exception:  # noqa: BLE001
            logger.exception("Snapshot capture raised (continuing): %s", rel_path)

    def _guarded_write(self, path: str, content: str) -> str:
        old = self.file_controller.current_text(path)
        if self.gate.request(path, old, content, kind="write"):
            self._snapshot(path, old, "write" if old else "create")
            return self.file_controller.write(path, content)
        return f"DENIED by user — {path} was NOT changed."

    def _guarded_append(self, path: str, content: str) -> str:
        old = self.file_controller.current_text(path)
        new = old + ("" if old.endswith("\n") or not old else "\n") + content
        if self.gate.request(path, old, new, kind="write"):
            self._snapshot(path, old, "append")
            return self.file_controller.write(path, new)
        return f"DENIED by user — {path} was NOT changed."

    def _guarded_move(self, src: str, dst: str) -> str:
        content = self.file_controller.read(src)
        if content.startswith("ERROR:"):
            return content
        old_dst = self.file_controller.current_text(dst)
        if self.gate.request(dst, old_dst, content, kind="write"):
            # Two snapshots: the destination (overwritten) and the source
            # (about to be deleted). Restore can rebuild either.
            self._snapshot(dst, old_dst, "write" if old_dst else "create")
            self._snapshot(src, content, "move")
            self.file_controller.write(dst, content)
            self.file_controller.delete(src)
            return f"moved {src} -> {dst}"
        return f"DENIED by user — {src} was NOT moved."

    def _godot_project_info(self) -> str:
        from vision.godot_project import read_project_info
        return read_project_info(self.config["agent"]["workspace_root"])

    def _godot_new_project(self, name: str) -> str:
        """Scaffold a fresh, valid Godot 4 project into an empty folder."""
        from vision.godot_project import scaffold_files
        if self.file_controller.current_text("project.godot"):
            return ("ERROR: a Godot project already exists here (project.godot present). "
                    "Refusing to overwrite. Point Settings at an EMPTY folder for a new game.")
        created = []
        for rel, content in scaffold_files(name):
            self.file_controller.write(rel, content)  # deterministic boilerplate
            created.append(rel)
        return f"Created a new Godot 4 project '{name}': {', '.join(created)}. Main scene = Main.tscn."

    def _tscn_create(self, a: dict) -> str:
        """Create a new, minimal valid scene file (then use tscn_add_node on it)."""
        path = a["path"]
        if not path.endswith(".tscn"):
            path += ".tscn"
        if self.file_controller.current_text(path):
            return f"ERROR: scene already exists: {path}"
        from pathlib import Path as _P
        root_name = a.get("root_name") or _P(path).stem
        root_type = a.get("root_type", "Node2D")
        content = f'[gd_scene format=3]\n\n[node name="{root_name}" type="{root_type}"]\n'
        if self.gate.request(path, "", content, kind="tscn"):
            self._snapshot(path, "", "create")
            self.file_controller.write(path, content)
            return f"Created scene {path} (root {root_name} : {root_type}). Add nodes with tscn_add_node."
        return f"DENIED by user — {path} not created."

    def _guarded_delete(self, path: str) -> str:
        old = self.file_controller.current_text(path)
        if self.gate.request(path, old, "", kind="delete"):
            self._snapshot(path, old, "delete")
            return self.file_controller.delete(path)
        return f"DENIED by user — {path} was NOT deleted."

    def _tscn_read(self, path: str) -> str:
        from vision.tscn_parser import TscnDocument, TscnError
        text = self.file_controller.read(path)
        if text.startswith("ERROR:"):
            return text
        try:
            return TscnDocument.parse(text).tree_summary()
        except TscnError as exc:
            return f"ERROR: {exc}"

    def _tscn_add_node(self, a: dict) -> str:
        from vision.tscn_parser import TscnDocument, TscnError
        path = a["path"]
        old = self.file_controller.current_text(path)
        if not old:
            return f"ERROR: scene not found: {path}"
        try:
            doc = TscnDocument.parse(old)
            doc.add_node(a["name"], a["type"], a.get("parent", "."), a.get("properties"))
            new = doc.serialize()
        except TscnError as exc:
            return f"ERROR: {exc}"
        if self.gate.request(path, old, new, kind="tscn"):
            self._snapshot(path, old, "write")
            self.file_controller.write(path, new)
            return f"Injected node '{a['name']}' ({a['type']}) into {path}."
        return f"DENIED by user — {path} unchanged."

    def _tscn_connect(self, a: dict) -> str:
        from vision.tscn_parser import TscnDocument, TscnError
        path = a["path"]
        old = self.file_controller.current_text(path)
        if not old:
            return f"ERROR: scene not found: {path}"
        try:
            doc = TscnDocument.parse(old)
            doc.add_connection(a["signal"], a.get("from", "."), a.get("to", "."), a["method"])
            new = doc.serialize()
        except TscnError as exc:
            return f"ERROR: {exc}"
        if self.gate.request(path, old, new, kind="tscn"):
            self._snapshot(path, old, "write")
            self.file_controller.write(path, new)
            return f"Connected signal '{a['signal']}' → {a['method']} in {path}."
        return f"DENIED by user — {path} unchanged."

    def _vault_search_tool(self, query: str, top_k: int) -> str:
        hits = self.vault.search(query, top_k=top_k)
        if not hits:
            return "(vault empty or no matches — run Reindex from the Vault menu)"
        return "\n\n".join(
            f"{h['path']}:{h['start']}-{h['end']} (score {h['score']})\n{h['text'][:500]}"
            for h in hits
        )

    # ------------------------------------------------------------------
    # Public API used by the UI
    # ------------------------------------------------------------------

    def submit_task(self, task: str) -> None:
        if self._worker and self._worker.isRunning():
            self.log.emit("warn", "Agent is already running a task — ignoring submit.")
            return

        # "continue" / "keep going" → resume the saved checkpoint, not a new task.
        if task.strip().lower() in self.CONTINUE_PHRASES and self.has_resumable():
            self.continue_task()
            return

        if self.online is False:
            self.failed.emit(
                "LM Studio isn't reachable yet. In LM Studio: load a model and click "
                "'Start Server' (port 1234). Connection status updates automatically — "
                "then resend your message."
            )
            return

        # Title the session from its first message (main thread → immediate UI update).
        cur = self.memory.current
        if cur and not cur.chat and cur.title in {"New session", "", None}:
            title = self.memory._derive_title(task)
            cur.title = title
            self.store.rename_session(cur.session_id, title)
            self.session_changed.emit(cur.session_id, title)

        self._launch_worker(task, resume_state=None)

    def continue_task(self) -> None:
        """Resume a paused/failed task from its persisted checkpoint."""
        if self._worker and self._worker.isRunning():
            self.log.emit("warn", "Agent is already running.")
            return
        cur = self.memory.current
        state = self.store.get_task_state(cur.session_id) if cur else None
        if not state:
            self.log.emit("warn", "Nothing to continue.")
            return
        if self.online is False:
            self.failed.emit("LM Studio isn't reachable — start the server, then continue.")
            return
        self.log.emit("info", "Resuming where the task left off…")
        self._launch_worker(state["user_task"], resume_state=state)

    def has_resumable(self) -> bool:
        cur = self.memory.current
        return bool(cur and self.store.get_task_state(cur.session_id))

    def get_roadmap(self) -> dict | None:
        return self.store.get_roadmap(self.config["agent"]["workspace_root"])

    # Phase 14b — per-project game design sheet (read + written by the Design
    # tab; the AI will read/update it in a later slice via a <<DESCRIPTION>> block).
    def get_description(self) -> dict | None:
        return self.store.get_description(self.config["agent"]["workspace_root"])

    def save_description(self, data: dict) -> None:
        self.store.save_description(self.config["agent"]["workspace_root"], data)

    def _conversation_summary(self, keep_recent: int) -> str:
        """Phase 14c — compact the older turns (those beyond the recent window)
        into a short summary so the agent keeps the gist of a long conversation
        even though only the last `keep_recent` messages are sent verbatim.
        Cached (recomputed only as more turns age out) and fully fail-safe: any
        error just returns "" and the agent proceeds windowed as before."""
        cur = self.memory.current
        if not cur:
            return ""
        older = cur.older_messages(keep_recent)
        if len(older) < 4:                      # not enough dropped history to bother
            return ""

        def _summarize() -> str:
            convo = "\n".join(f"{m.role}: {m.content[:600]}" for m in older)[-8000:]
            prompt = [
                ChatMessage(role="system", content=(
                    "Summarise the earlier part of this Godot game-dev conversation in "
                    "6-10 tight bullet points: decisions made, what was built, open threads, "
                    "and anything the assistant must remember. No preamble.")),
                ChatMessage(role="user", content=convo),
            ]
            try:
                return (self.client.complete(prompt, max_tokens=320, temperature=0.2) or "").strip()
            except Exception:  # noqa: BLE001
                return ""

        try:
            if self.context_cache is not None:
                # Key on the count of aged-out messages: a cache hit until more turns drop.
                key = signature("convsum", cur.session_id, len(older))
                summary = self.context_cache.get_or_compute(key, _summarize, ttl=1800) or ""
            else:
                summary = _summarize()
        except Exception:  # noqa: BLE001
            summary = ""
        return f"EARLIER IN THIS SESSION (compacted summary):\n{summary}" if summary else ""

    def _launch_worker(self, task: str, resume_state: dict | None) -> None:
        # Resolve the per-turn budget from the VRAM saver (mode = pref + free VRAM).
        budget = self.vram_saver.budget(self._base_max_tokens)
        mode = self.vram_saver.current_mode()
        self.client.config.max_tokens = budget.max_output_tokens
        if self.vram_saver.mode_changed_since_last_check():
            # Only spam the log on actual transitions, not every turn.
            self.log.emit("info", f"VRAM saver: mode = {mode.label}.")
            # Phase 6: on a tighter-than-FULL transition, tell the user how to
            # spill the model into RAM (and optionally do it for them).
            try:
                plan = self.vram_saver.spill_plan()
                if plan.gpu_offload < 1.0:
                    self.log.emit("info",
                        "Low-VRAM tuning — " + lmstudio_tuning.guidance_for(plan, self._low_vram_model))
                    if self._spill_apply:
                        res = lmstudio_tuning.apply_offload(plan, self._low_vram_model)
                        self.log.emit("info" if res.get("applied") else "warn",
                                      "Spill apply: " + res.get("message", ""))
            except Exception:  # noqa: BLE001 — tuning is advisory, never fatal
                pass
        self.vram_mode_changed.emit(mode.label)
        if budget.max_output_tokens < self._base_max_tokens:
            self.log.emit(
                "warn",
                f"VRAM saver clamped max output tokens to {budget.max_output_tokens} "
                f"(mode: {mode.label}).",
            )

        # Workspace tree → shallower + truncated when the budget is tight.
        workspace_summary = self.file_controller.list_tree(depth=budget.workspace_tree_depth)
        workspace_summary = truncate_tree(workspace_summary, budget.workspace_tree_max_lines)

        # RAG hits from the Vault (CPU, instant) — fewer + shorter under pressure.
        try:
            rag = self.vault.search_context(
                task,
                top_k=budget.rag_top_k,
                max_chars=budget.rag_max_chars,
            )
        except Exception:  # noqa: BLE001
            rag = ""
        if rag:
            workspace_summary = f"{workspace_summary}\n\n{rag}"
            self.log.emit("info", "Vault: injected relevant code into planning context.")

        # Solution recall — surface past fixes for similar problems. Zero VRAM cost
        # (CPU vectoriser over the curated evolution log).
        #
        # Phase 6: in lean/minimal VRAM modes we cache the *rendered* recall
        # block to disk (keyed by task + depth) and reuse it, so we don't
        # re-run the vectoriser and re-derive context every turn under memory
        # pressure. FULL mode always recomputes fresh. Fail-safe: any hiccup
        # falls back to a direct recall.
        def _render_recall() -> str:
            try:
                rs = self.solution_recall.recall(task, top_k=budget.solution_recall_k)
            except Exception:  # noqa: BLE001
                return ""
            return self.solution_recall.render_prompt_block(rs) if rs else ""

        recall_block = ""
        try:
            cache_this = (self.context_cache is not None
                          and self.vram_saver.spill_plan().aggressive_cache)
            if cache_this:
                key = signature("recall", task, budget.solution_recall_k)
                recall_block = self.context_cache.get_or_compute(
                    key, _render_recall, ttl=300) or ""
            else:
                recall_block = _render_recall()
        except Exception:  # noqa: BLE001
            recall_block = _render_recall()
        if recall_block:
            workspace_summary = f"{workspace_summary}\n\n{recall_block}"
            self.log.emit("info", "Recalled past solution(s) into context.")

        # Phase 14c — fold the older turns into a compacted summary so a long
        # conversation keeps its gist (only the last history_max_messages turns
        # are sent verbatim). Cached + fail-safe.
        conv_summary = self._conversation_summary(budget.history_max_messages)
        if conv_summary:
            workspace_summary = f"{workspace_summary}\n\n{conv_summary}"
            self.log.emit("info", "Compacted earlier conversation into context.")

        worker = _AgentWorker(
            user_task=task,
            reasoner=self.reasoner,
            tools=self.tools,
            memory=self.memory,
            workspace_summary=workspace_summary,
            max_tool_iterations=self.config["agent"]["max_tool_iterations"],
            question_gate=self.questions,
            resume_state=resume_state,
            history_max_messages=budget.history_max_messages,
        )
        worker.stage_chunk.connect(self.stage_chunk)
        worker.stage_done.connect(self.stage_done)
        worker.tool_invoked.connect(self.tool_invoked)
        worker.tool_result.connect(self.tool_result)
        worker.final_answer.connect(self._on_final_answer)
        worker.log.connect(self.log)
        worker.failed.connect(self._on_failed)
        worker.progress.connect(self.progress)
        worker.roadmap_updated.connect(self.roadmap_updated)
        worker.description_updated.connect(self.description_updated)
        worker.paused.connect(self._on_paused)
        worker.connection_lost.connect(self._on_connection_lost)
        worker.finished.connect(self._on_worker_finished)

        self._worker = worker
        self.busy_changed.emit(True)
        self.progress.emit(0, "Starting…")
        worker.start()

    def _on_worker_finished(self) -> None:
        self.busy_changed.emit(False)
        self.resumable_changed.emit(self.has_resumable())

    def _on_paused(self, hint: str) -> None:
        self.task_paused.emit(hint)

    def _on_connection_lost(self, reason: str) -> None:
        """Worker hit a transient LM Studio error. Flip into reconnect mode:
        emit a banner-worthy signal, kick off a fast polling cadence, and
        auto-continue the task the moment health_check returns OK."""
        self._reconnect_pending = True
        self.connection_lost.emit(reason)
        self.log.emit("warn", f"LM Studio dropped — reconnecting… ({reason[:120]})")
        # Use the existing connection probe; speed up to 3s so the user doesn't
        # wait long once LM Studio is back. We restore the slow cadence in
        # _on_conn_result when we go online.
        if self._reconnect_timer is None:
            from PyQt6.QtCore import QTimer
            self._reconnect_timer = QTimer(self)
            self._reconnect_timer.setInterval(3000)
            self._reconnect_timer.timeout.connect(self.refresh_connection_async)
        if not self._reconnect_timer.isActive():
            self._reconnect_timer.start()
        # Prime an immediate probe so the user doesn't have to wait a tick.
        self.refresh_connection_async()

    def cancel(self) -> None:
        # Cancel also clears any pending reconnect — the user said "stop", and
        # we'd be rude to auto-resume an hour later.
        if self._reconnect_pending:
            self._reconnect_pending = False
            if self._reconnect_timer and self._reconnect_timer.isActive():
                self._reconnect_timer.stop()
            self.log.emit("warn", "Reconnect-wait cancelled.")
        if self._worker and self._worker.isRunning():
            self.log.emit("warn", "Stopping agent…")
            self._worker.requestInterruption()
            # Release any blocked diff/question prompt so the worker can exit.
            self.gate.cancel_all()
            self.questions.cancel_all()
            # Give the loop a moment to exit cleanly at its next checkpoint.
            if not self._worker.wait(1500):
                self._worker.terminate()
                self._worker.wait(1500)
            self.busy_changed.emit(False)
            self.resumable_changed.emit(self.has_resumable())
            self.log.emit("warn", "Agent task cancelled.")

    def refresh_connection_async(self) -> None:
        """Kick off a non-blocking connection probe; result arrives via connection_changed."""
        if self._conn_worker and self._conn_worker.isRunning():
            return
        worker = _ConnCheckWorker(self.client)
        worker.result.connect(self._on_conn_result)
        self._conn_worker = worker
        worker.start()

    def _on_conn_result(self, ok: bool, msg: str) -> None:
        self.online = ok
        self.connection_changed.emit(ok, msg)
        # If a task is parked waiting for LM Studio and the server just came
        # back, stop the fast-cadence poll and auto-resume the task.
        if ok and self._reconnect_pending:
            self._reconnect_pending = False
            if self._reconnect_timer and self._reconnect_timer.isActive():
                self._reconnect_timer.stop()
            self.connection_restored.emit()
            self.log.emit("ok", "LM Studio is back — resuming the paused task.")
            # Defer to the next event-loop tick so connection_changed listeners
            # (status bar, banners) get to update before we burn the screen
            # with a fresh agent run.
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(200, self._safe_continue_after_reconnect)

    def _safe_continue_after_reconnect(self) -> None:
        # Only continue if there's actually a paused task and no other worker
        # has started in the meantime (e.g. user kicked off something new).
        if self._worker and self._worker.isRunning():
            return
        if not self.has_resumable():
            return
        try:
            self.continue_task()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Auto-continue after reconnect failed: %s", exc)
            self.failed.emit(f"Auto-continue after reconnect failed: {exc}")

    def request_advice_async(self, prompt: str) -> None:
        """Run a proactive-advice query off-thread. Result arrives via advice_ready."""
        if self.online is not True:
            return
        if self._advisor and self._advisor.isRunning():
            return
        worker = _AdvisorWorker(self.client, prompt)
        worker.advice_ready.connect(self.advice_ready)
        self._advisor = worker
        worker.start()

    # ----- The Vault (reindex on a worker thread) -------------------------

    def reindex_vault_async(self) -> None:
        if self._vault_worker and self._vault_worker.isRunning():
            self.log.emit("warn", "Vault reindex already running.")
            return

        vault = self.vault
        log = self.log
        done = self.vault_reindexed

        class _VaultWorker(QThread):
            result = pyqtSignal(int)

            def run(self):
                try:
                    n = vault.reindex()
                    self.result.emit(n)
                except Exception as exc:  # noqa: BLE001
                    log.emit("error", f"Vault reindex failed: {exc}")
                    self.result.emit(-1)

        worker = _VaultWorker()
        self.log.emit("info", "Vault: indexing project files…")

        def _on_done(n: int):
            if n >= 0:
                stats = self.vault.stats()
                done.emit(n, f"Vault indexed {n} chunks across {stats['files']} files.")
                self.log.emit("ok", f"Vault ready — {n} chunks, {stats['files']} files.")

        worker.result.connect(_on_done)
        self._vault_worker = worker
        worker.start()

    # ----- Self-Healing ---------------------------------------------------

    def set_self_heal(self, enabled: bool) -> None:
        self.self_heal_enabled = enabled
        if enabled:
            self._start_error_watcher()
        else:
            self._stop_error_watcher()

    def _start_error_watcher(self) -> None:
        if self._error_watcher and self._error_watcher.isRunning():
            return
        from vision.godot_log import GodotErrorWatcher
        watcher = GodotErrorWatcher(
            godot_bridge=self.godot,
            log_path=self.config.get("godot", {}).get("log_path", ""),
            poll_seconds=self.config.get("vision", {}).get("self_heal_poll_seconds", 8),
        )
        watcher.error_detected.connect(self._on_godot_error)
        watcher.status.connect(lambda m: self.log.emit("info", m))
        self._error_watcher = watcher
        watcher.start()
        self.log.emit("ok", "Self-Healing armed.")

    def _stop_error_watcher(self) -> None:
        w = self._error_watcher
        if w and w.isRunning():
            w.requestInterruption()
            if not w.wait(1500):
                w.terminate()
                w.wait(1000)
        self._error_watcher = None
        self.log.emit("info", "Self-Healing disarmed.")

    def _on_godot_error(self, source: str, text: str) -> None:
        self.godot_error.emit(source, text)
        self.log.emit("error", f"[{source}] Godot error detected:\n{text[:300]}")
        # Auto-dispatch a fix if we're idle and online; submit_task no-ops if busy.
        if self.self_heal_enabled and self.online and not (self._worker and self._worker.isRunning()):
            fix_task = (
                "SELF-HEAL: Godot reported an error. Diagnose the root cause in the "
                "project files and fix it. Use vault_search and file_read to locate the "
                f"relevant code first.\n\nError ({source}):\n{text}"
            )
            self.submit_task(fix_task)

    # ----- Phoenix Protocol (self-upgrade) --------------------------------

    def start_evolution(self, mode: str = "synthetic", dry_run: bool = True) -> None:
        if self._evo_worker and self._evo_worker.isRunning():
            self.log.emit("warn", "An evolution cycle is already running.")
            return
        from core.evolution import EvolutionWorker
        worker = EvolutionWorker(self.evolution, mode=mode, dry_run=dry_run)
        worker.status.connect(self.evolution_status)
        worker.log.connect(self.log)
        worker.candidate_ready.connect(self._on_evo_candidate)
        worker.failed.connect(self._on_evo_failed)
        worker.cycle_done.connect(self.evolution_done)
        self._evo_worker = worker
        self.evolution_status.emit("Igniting Phoenix Protocol…")
        worker.start()

    def _evo_mode(self) -> str:
        return self._evo_worker.mode if self._evo_worker else "llm"

    def _on_evo_candidate(self, cand: dict) -> None:
        self.store.log_evolution(
            self._evo_mode(), cand["rationale"], cand["changed"],
            validation=True, swapped=False, dry_run=cand["dry_run"],
            detail=cand["validation_log"],
        )
        self.evolution_candidate.emit(cand)

    def _on_evo_failed(self, msg: str) -> None:
        self.store.log_evolution(
            self._evo_mode(), "(cycle failed)", [],
            validation=False, swapped=False, dry_run=True, detail=msg,
        )
        self.evolution_failed.emit(msg)

    def apply_candidate(self, cand: dict) -> list[str]:
        from pathlib import Path
        swapped = self.evolution.swap(Path(cand["shadow_dir"]), cand["changed"])
        self.store.log_evolution(
            self._evo_mode(), cand["rationale"], swapped,
            validation=True, swapped=True, dry_run=False, detail="hot-swapped",
        )
        self.log.emit("ok", f"Hot-swapped {len(swapped)} file(s): {', '.join(swapped)}")
        return swapped

    def list_evolution(self) -> list[dict]:
        return self.store.list_evolution()

    def hardware_line(self) -> str:
        return self.hardware.format_line()

    # ------------------------------------------------------------------
    # Snapshot restore (Snapshots tab calls these)
    # ------------------------------------------------------------------

    def restore_snapshot(self, snapshot_id: str) -> str:
        """Restore a single file from a snapshot, with the same approval diff
        we use for an agent write. Returns a status string for the log.

        The restore itself is also snapshotted — that way "I restored from X
        and then changed my mind" can be undone too.
        """
        meta = self.snapshots.get(snapshot_id)
        if not meta:
            return f"Snapshot {snapshot_id} not found."
        old_for_restore = self.snapshots.read_content(snapshot_id) or ""
        current = self.file_controller.current_text(meta.path)
        if current == old_for_restore:
            return f"{meta.path} is already at this snapshot — nothing to restore."
        if self.gate.request(meta.path, current, old_for_restore, kind="restore"):
            # Capture what we're about to overwrite, just in case the user
            # changes their mind about restoring.
            self._snapshot(meta.path, current, "restore")
            self.file_controller.write(meta.path, old_for_restore)
            return f"Restored {meta.path} from snapshot {snapshot_id}."
        return f"Restore of {meta.path} was cancelled."

    def restore_session(self, session_id: int) -> list[str]:
        """Restore every file touched in ``session_id`` to its earliest
        captured state. Each file individually goes through the approval gate,
        so the user can approve / skip per file. Returns the result string
        list (one per attempted file)."""
        from .snapshots import build_session_restore_plan
        plan = build_session_restore_plan(self.snapshots, session_id)
        if not plan:
            return [f"No snapshots found for session {session_id}."]
        results: list[str] = []
        for item in plan:
            current = self.file_controller.current_text(item.meta.path)
            if current == item.old_content:
                results.append(f"{item.meta.path}: already at original.")
                continue
            if self.gate.request(item.meta.path, current, item.old_content, kind="restore"):
                self._snapshot(item.meta.path, current, "restore")
                self.file_controller.write(item.meta.path, item.old_content)
                results.append(f"{item.meta.path}: restored.")
            else:
                results.append(f"{item.meta.path}: skipped.")
        return results

    def shutdown(self) -> None:
        """Stop every background thread cleanly so Qt doesn't destroy a running QThread."""
        self.gate.cancel_all()
        self.questions.cancel_all()
        self._stop_error_watcher()
        if self._reconnect_timer is not None:
            try:
                self._reconnect_timer.stop()
            except Exception:  # noqa: BLE001
                pass
        for w in (self._worker, self._conn_worker, self._advisor,
                  self._vault_worker, self._evo_worker):
            if w and w.isRunning():
                w.requestInterruption()
                if not w.wait(1500):
                    w.terminate()
                    w.wait(1000)
        try:
            self.hardware.close()
        except Exception:  # noqa: BLE001
            pass

    def check_elevation(self) -> dict:
        """Return self vs. Godot integrity levels and whether input is UIPI-blocked."""
        from core.elevation import (
            IntegrityLevel,
            elevation_mismatch,
            is_admin,
            self_integrity_level,
            window_integrity_level,
        )
        hwnd = self.godot.find_hwnd()
        return {
            "is_admin": is_admin(),
            "self_level": self_integrity_level().label,
            "godot_found": hwnd is not None,
            "godot_level": window_integrity_level(hwnd).label,
            "mismatch": elevation_mismatch(hwnd),
        }

    def relaunch_with_uac(self) -> bool:
        from core.elevation import relaunch_as_admin
        return relaunch_as_admin()

    # ------------------------------------------------------------------
    # Settings (configure everything from the UI — no code editing)
    # ------------------------------------------------------------------

    def apply_settings(self, new_cfg: dict) -> None:
        """Persist new config to config.json (atomically) and apply it live."""
        import json
        from core.atomic import atomic_write
        atomic_write(self.config_path, json.dumps(new_cfg, indent=2))
        self.reconfigure(new_cfg)

    def reconfigure(self, new_cfg: dict) -> None:
        """Rebuild the live components from a new config without restarting.
        Refuses while a task is running (avoid swapping the agent out mid-flight)."""
        if self._worker and self._worker.isRunning():
            raise RuntimeError("Stop the running task before changing settings.")

        self.config = new_cfg

        # LM Studio client (URL / model / temperature / max_tokens / timeout)
        lm = LMStudioConfig.from_dict(new_cfg["lm_studio"])
        self.client = LMStudioClient(lm)
        self.reasoner = ChainOfThoughtReasoner(self.client)
        self.evolution.client = self.client
        self._base_max_tokens = lm.max_tokens
        self.online = None  # force a fresh connection probe

        # VRAM saver preference (auto / off / lean / minimal). Mode is recomputed
        # on every task launch, so this just stores the user's intent.
        new_vram_pref = (new_cfg.get("vram_saver") or {}).get("mode", "auto")
        self.vram_saver.set_pref(new_vram_pref)
        self.vram_mode_changed.emit(self.vram_saver.current_mode().label)

        # Workspace-dependent components (tool lambdas read these at call time)
        from tools.file_controller import FileController
        from db.vault import Vault
        from vision.signal_mapper import SignalMapper
        ws = new_cfg["agent"]["workspace_root"]
        self.file_controller = FileController(ws)
        self.vault = Vault(new_cfg["database"].get("vault_path", "db/vault.db"), ws)
        self.signal_mapper = SignalMapper(ws)
        # Snapshots are per-project — re-point at the new workspace so a
        # restore browsed in the new project doesn't accidentally show entries
        # from the previous one.
        self.snapshots.set_workspace(ws)

        # Vision / Godot
        self.vision.config = new_cfg.get("vision", {})
        self.godot.title_match = new_cfg["vision"].get("godot_window_title_match", "Godot")

        # Restart the self-heal watcher so it picks up a new log path
        if self._error_watcher and self._error_watcher.isRunning():
            self._stop_error_watcher()
            self._start_error_watcher()

        self.log.emit("ok", f"Settings applied — project: {ws}")
        self.workspace_changed.emit(ws)

        # Auto-index the new project so the agent immediately knows the codebase (RAG).
        try:
            self.reindex_vault_async()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Session management (history + cross-session resume)
    # ------------------------------------------------------------------

    @property
    def current_session_id(self) -> int | None:
        return self.memory.current.session_id if self.memory.current else None

    def list_session_summaries(self) -> list[dict]:
        return self.store.list_session_summaries()

    def new_session(self) -> int:
        """Start a fresh session. Reuses an existing empty 'New session' if present."""
        cur = self.memory.current
        if (
            cur
            and self.store.get_session(cur.session_id) is not None  # not deleted
            and self.store.is_session_empty(cur.session_id)
        ):
            # Already on a blank, live session — just reuse it.
            self.session_changed.emit(cur.session_id, cur.title)
            return cur.session_id
        ctx = self.memory.start_session(
            title="New session",
            workspace_root=self.config["agent"]["workspace_root"],
        )
        self.session_changed.emit(ctx.session_id, ctx.title)
        return ctx.session_id

    def open_session(self, session_id: int) -> list[dict]:
        """Resume a past session (possibly from another run) and return its messages
        as [{'role','content','metadata'}] for the UI to render."""
        sess = self.store.get_session(session_id)
        if not sess:
            return []
        self.memory.resume_session(
            session_id=session_id,
            title=sess.title,
            workspace_root=sess.workspace or self.config["agent"]["workspace_root"],
        )
        self.session_changed.emit(session_id, sess.title)
        msgs = self.store.get_messages(session_id)
        return [
            {"role": m.role, "content": m.content, "metadata": m.metadata}
            for m in msgs
        ]

    def delete_session(self, session_id: int) -> None:
        self.store.delete_session(session_id)
        if self.current_session_id == session_id:
            self.new_session()

    # ------------------------------------------------------------------

    def _on_final_answer(self, answer: str) -> None:
        self.final_answer.emit(answer)

    def _on_failed(self, err: str) -> None:
        self.failed.emit(err)
        self.busy_changed.emit(False)


def _short(d: dict, limit: int = 120) -> str:
    s = json.dumps(d, default=str)
    return s if len(s) <= limit else s[:limit] + "…"
