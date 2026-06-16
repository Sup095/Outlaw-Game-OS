"""Main window for Outlaw CodeMaker.

Layout:
    +---------------------------------------------------------------+
    | Menu  (Session · Tools · View · Help)                         |
    +---------------+-----------------------+-----------------------+
    | Workspace     | Thought Chamber       | Chat                  |
    |  tree         |  PLAN                 | (user ↔ assistant)    |
    |               |  REVIEW               |                       |
    |               |  EXECUTE              |                       |
    |               |                       |                       |
    |               |                       +-----------------------+
    |               |                       | Terminal log          |
    +---------------+-----------------------+-----------------------+
    | [ prompt input ........................... ] [Send] [Stop]   |
    +---------------------------------------------------------------+
    | status: LM Studio connected · workspace=… · 12 files          |
    +---------------------------------------------------------------+

Proactive Advisory Mode: a QTimer fires every N seconds. If the agent is idle
and the workspace has changed, it asks the model for a single suggestion and
surfaces it as a non-modal banner with Accept / Dismiss buttons.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QTextCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.orchestrator import Orchestrator
from db.projects import ProjectsStore

from .description_view import DescriptionView
from .roadmap_view import RoadmapView
from .session_browser import SessionBrowser
from .snapshots_view import SnapshotsView
from .steam_panel import SteamPanel
from .terminal_log import TerminalLog
from .thought_chamber import ThoughtChamber
from .workspace_view import WorkspaceView


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-widgets
# ---------------------------------------------------------------------------


class ChatPanel(QWidget):
    """Right-side conversation panel with markdown/code rendering."""

    ROLE_STYLE = {
        "USER":   ("#f4d27a", "#1a1d27"),
        "OUTLAW": ("#5cc97a", "#121a16"),
        "SYS":    ("#838c9c", "#13171f"),
        "ADVICE": ("#e8a84c", "#221b10"),
        "TOOL":   ("#6aa6e8", "#0f1620"),
    }

    def __init__(self):
        super().__init__()
        header = QFrame()
        header.setObjectName("PanelHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        title = QLabel("CHAT")
        title.setObjectName("PanelTitle")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet("color:#838c9c;")
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(self.subtitle)

        self.view = QTextEdit()
        self.view.setObjectName("CodeView")
        self.view.setReadOnly(True)
        self.view.setAcceptRichText(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.view, 1)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)

    def clear(self) -> None:
        self.view.clear()

    def add_user(self, text: str) -> None:
        self._append("USER", text)

    def add_assistant(self, text: str) -> None:
        self._append("OUTLAW", text)

    def add_system(self, text: str) -> None:
        self._append("SYS", text)

    def add_advice(self, text: str) -> None:
        self._append("ADVICE", text)

    def add_tool(self, text: str) -> None:
        self._append("TOOL", text)

    def render_history(self, messages: list[dict]) -> None:
        """Render a resumed session's messages."""
        self.clear()
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "user":
                self.add_user(content)
            elif role == "assistant":
                self.add_assistant(content)
            elif role == "tool":
                tool = (m.get("metadata") or {}).get("tool", "tool")
                self.add_tool(f"`{tool}` → {content[:300]}")
            else:
                self.add_system(content)

    def _append(self, who: str, text: str) -> None:
        from .formatting import to_html
        color, bubble = self.ROLE_STYLE.get(who, ("#dde3ea", "#13171f"))
        body = to_html(text)
        block = (
            f'<table width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin:6px 0;"><tr><td style="background:{bubble};'
            f'border-left:3px solid {color};border-radius:8px;padding:8px 12px;">'
            f'<div style="color:{color};font-weight:700;font-size:8pt;'
            f'letter-spacing:1px;margin-bottom:3px;">{who}</div>'
            f'<div style="color:#dde3ea;">{body}</div>'
            f'</td></tr></table>'
        )
        self.view.append(block)
        self.view.moveCursor(QTextCursor.MoveOperation.End)


class AdvisoryBanner(QFrame):
    """Non-modal proactive suggestion strip pinned above the input bar."""

    def __init__(self, on_accept, on_dismiss):
        super().__init__()
        self.setObjectName("PanelHeader")
        self.setStyleSheet(
            "QFrame { background-color: #2b2316; border: 1px solid #d4a017; border-radius: 4px; }"
            "QLabel#advice { color: #f0d27a; }"
        )
        self.setVisible(False)

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 8, 10, 8)
        self.label = QLabel()
        self.label.setObjectName("advice")
        self.label.setWordWrap(True)
        accept = QPushButton("Do it")
        accept.setObjectName("PrimaryButton")
        accept.clicked.connect(lambda: (on_accept(self.current_advice), self.hide()))
        dismiss = QPushButton("Dismiss")
        dismiss.clicked.connect(lambda: (on_dismiss(), self.hide()))
        h.addWidget(self.label, 1)
        h.addWidget(accept)
        h.addWidget(dismiss)

        self.current_advice = ""

    def show_advice(self, text: str) -> None:
        self.current_advice = text
        self.label.setText(f"💡 {text}")
        self.setVisible(True)


class ProgressHud(QWidget):
    """Multi-stage heist HUD: SCOUT → PLAN → WRITE → VERIFY chips + a % bar."""

    STAGES = ["SCOUT", "PLAN", "WRITE", "VERIFY"]

    def __init__(self):
        super().__init__()
        self.chips: list[QLabel] = []
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(4)
        for i, name in enumerate(self.STAGES):
            chip = QLabel(f"{i+1} {name}")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFixedHeight(18)
            self.chips.append(chip)
            chip_row.addWidget(chip)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("idle")
        self.bar.setFixedWidth(320)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addLayout(chip_row)
        col.addWidget(self.bar)
        self._paint_chips(-1)

    def _stage_index(self, pct: int, label: str) -> int:
        low = (label or "").lower()
        if any(k in low for k in ("scout", "start", "vault")):
            return 0
        if any(k in low for k in ("plan", "review")):
            return 1
        if any(k in low for k in ("execut", "act", "tool", "writ")):
            return 2
        if any(k in low for k in ("final", "verif", "done", "iteration")):
            return 3
        return min(3, pct // 25)

    def update(self, pct: int, label: str) -> None:
        self.bar.setValue(max(0, min(100, pct)))
        self.bar.setFormat(f"{label}  {pct}%" if label else f"{pct}%")
        self._paint_chips(self._stage_index(pct, label))

    def reset(self) -> None:
        self.bar.setValue(0)
        self.bar.setFormat("idle")
        self._paint_chips(-1)

    def _paint_chips(self, active: int) -> None:
        for i, chip in enumerate(self.chips):
            if active < 0:
                fg, bg, bd = "#5a6573", "#11151f", "#2a3242"      # idle
            elif i < active:
                fg, bg, bd = "#0e1116", "#7d6526", "#7d6526"      # done
            elif i == active:
                fg, bg, bd = "#1a1407", "#f4d27a", "#f4d27a"      # active
            else:
                fg, bg, bd = "#5a6573", "#11151f", "#2a3242"      # pending
            weight = "700" if i == active else "600"
            chip.setStyleSheet(
                f"QLabel{{color:{fg};background:{bg};border:1px solid {bd};"
                f"border-radius:8px;padding:1px 8px;font-size:8pt;font-weight:{weight};}}"
            )


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(
        self,
        orchestrator: Orchestrator,
        config: dict,
        projects_store: ProjectsStore | None = None,
    ):
        super().__init__()
        self.orch = orchestrator
        self.config = config
        # Optional so the window still runs in older callers / tests that don't
        # pre-build a registry. main.py always provides one.
        self.projects_store = projects_store
        self._pending_kickoff: str | None = None

        self.setWindowTitle("Outlaw CodeMaker")
        self.resize(config["ui"]["window_width"], config["ui"]["window_height"])

        # Live "working on" headline state — populated by _on_send, consumed by
        # the busy_changed handler. Default is the static tagline shown when idle.
        self._idle_tagline = "autonomous Godot agent · plan → review → execute"
        self._current_task_summary: str = ""

        self._build_ui()
        self._wire_signals()
        self._build_menu()
        self._build_shortcuts()
        self._start_proactive_timer()
        self._build_notifier()

        self._last_conn_ok: bool | None = None

        # Hook the history browser up to the store and show current sessions.
        self.session_browser.bind(self.orch.list_session_summaries)
        self.session_browser.set_current(self.orch.current_session_id)
        self.session_browser.refresh()

        # Roadmap reads the current project's saved plan.
        self.roadmap_view.bind(self.orch.get_roadmap)
        self.roadmap_view.refresh()
        # Design sheet reads/writes the current project's description (Phase 14b).
        self.description_view.bind(self.orch.get_description, self.orch.save_description)
        # Show the Continue button if a paused task exists from a previous run.
        self.continue_btn.setVisible(self.orch.has_resumable())

        # Arm self-heal if it was enabled in config.
        if self.orch.self_heal_enabled:
            self.orch.set_self_heal(True)

        self._show_welcome()
        QTimer.singleShot(600, self._maybe_first_run)  # gentle setup prompt for newcomers

        # Initial (non-blocking) connection + elevation checks
        QTimer.singleShot(300, self.orch.refresh_connection_async)
        QTimer.singleShot(800, self._refresh_elevation)

        # Poll the connection every 5s until the server comes up, then it just
        # confirms it stays up. All probing happens on a background thread.
        self._conn_timer = QTimer(self)
        self._conn_timer.setInterval(5_000)
        self._conn_timer.timeout.connect(self.orch.refresh_connection_async)
        self._conn_timer.start()

        # Hardware (VRAM/RAM) readout, polled every 2s.
        self._hw_timer = QTimer(self)
        self._hw_timer.setInterval(2_000)
        self._hw_timer.timeout.connect(self._refresh_hardware)
        self._hw_timer.start()
        QTimer.singleShot(400, self._refresh_hardware)
        self._regenerate_on = False
        self._evo_pending = False

        # Refresh elevation every 10s so the status bar reacts when the user
        # opens Godot (or relaunches it as admin) after our app is already running.
        self._elev_timer = QTimer(self)
        self._elev_timer.setInterval(10_000)
        self._elev_timer.timeout.connect(self._refresh_elevation)
        self._elev_timer.start()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        outer.addWidget(self._build_header())

        # Left: tabbed Workspace | History | Roadmap | Snapshots | Steam
        self.workspace_view = WorkspaceView(self.config["agent"]["workspace_root"])
        self.session_browser = SessionBrowser()
        self.roadmap_view = RoadmapView()
        self.description_view = DescriptionView()
        self.snapshots_view = SnapshotsView(self.orch)
        self.steam_panel = SteamPanel(self.config["agent"]["workspace_root"])
        self.steam_panel.log_message.connect(
            lambda level, msg: self.terminal.append(level, f"[steam] {msg}")
        )
        self.left_tabs = QTabWidget()
        self.left_tabs.addTab(self.workspace_view, "Workspace")
        self.left_tabs.addTab(self.session_browser, "History")
        self.left_tabs.addTab(self.roadmap_view, "Roadmap")
        self.left_tabs.addTab(self.description_view, "Design")
        self.left_tabs.addTab(self.snapshots_view, "Snapshots")
        self.left_tabs.addTab(self.steam_panel, "Steam")
        # Refresh a tab's contents whenever it's opened, so it's never stale.
        self.left_tabs.currentChanged.connect(self._on_left_tab_changed)

        self.thought_chamber = ThoughtChamber()
        self.chat_panel = ChatPanel()
        self.terminal = TerminalLog()

        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.addWidget(self.chat_panel)
        right_split.addWidget(self.terminal)
        right_split.setSizes([720, 280])

        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(self.left_tabs)
        main_split.addWidget(self.thought_chamber)
        main_split.addWidget(right_split)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 2)
        main_split.setStretchFactor(2, 2)
        main_split.setSizes([280, 680, 640])

        outer.addWidget(main_split, 1)

        # Advisory banner (hidden by default)
        self.banner = AdvisoryBanner(
            on_accept=self._accept_advice,
            on_dismiss=self._dismiss_advice,
        )
        outer.addWidget(self.banner)

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Describe what you want — e.g. 'add a state machine to Player.gd'")
        self.input.returnPressed.connect(self._on_send)

        self.send_btn = QPushButton("Send  ➤")
        self.send_btn.setObjectName("PrimaryButton")
        self.send_btn.setMinimumWidth(96)
        self.send_btn.clicked.connect(self._on_send)

        self.continue_btn = QPushButton("▸ Continue")
        self.continue_btn.setObjectName("PrimaryButton")
        self.continue_btn.setToolTip("Pick up the paused task where it left off")
        self.continue_btn.clicked.connect(self._on_continue)
        self.continue_btn.setVisible(False)

        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.orch.cancel)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setMinimumWidth(80)

        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.continue_btn)
        input_row.addWidget(self.send_btn)
        input_row.addWidget(self.stop_btn)
        outer.addLayout(input_row)

        self.setCentralWidget(central)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.session_label = QLabel("session: new")
        self.hardware_label = QLabel("hw: …")
        self.vram_mode_label = QLabel("VRAM: …")
        self.vram_mode_label.setToolTip("Active VRAM saver mode (Auto/Lean/Minimal). Tighter modes shrink "
                                       "the workspace tree, RAG hits, history, and max output tokens so the "
                                       "model doesn't OOM mid-generation. Change in Settings.")
        self.model_label = QLabel("model: —")
        self.workspace_label = QLabel(f"workspace: {self.config['agent']['workspace_root']}")
        sb.addWidget(self.session_label)
        sb.addPermanentWidget(self.hardware_label)
        sb.addPermanentWidget(self.vram_mode_label)
        sb.addPermanentWidget(self.model_label)
        sb.addPermanentWidget(self.workspace_label)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("AppHeader")
        header.setFixedHeight(64)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 8, 16, 8)
        h.setSpacing(14)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel("⟁  OUTLAW CODEMAKER")
        title.setObjectName("AppTitle")
        # The tagline doubles as the "working on" headline: idle copy when no
        # task is running, replaced live with the current task while busy.
        self.tagline_label = QLabel(self._idle_tagline)
        self.tagline_label.setObjectName("AppTagline")
        self.tagline_label.setTextFormat(Qt.TextFormat.RichText)
        brand.addWidget(title)
        brand.addWidget(self.tagline_label)

        self.conn_label = QLabel("LM Studio: checking…")
        self.conn_label.setTextFormat(Qt.TextFormat.RichText)
        self.elev_label = QLabel("elevation: …")
        self.elev_label.setTextFormat(Qt.TextFormat.RichText)

        status_col = QVBoxLayout()
        status_col.setSpacing(1)
        status_col.addWidget(self.conn_label, 0, Qt.AlignmentFlag.AlignRight)
        status_col.addWidget(self.elev_label, 0, Qt.AlignmentFlag.AlignRight)

        # Multi-stage progress HUD (chips + bar). progress_bar kept as an alias.
        self.hud = ProgressHud()
        self.progress_bar = self.hud.bar

        self.phoenix_label = QLabel("")
        self.phoenix_label.setTextFormat(Qt.TextFormat.RichText)

        h.addLayout(brand, 0)
        h.addStretch(1)
        h.addWidget(self.phoenix_label, 0)
        h.addLayout(status_col, 0)
        h.addWidget(self.hud, 0)
        return header

    def _build_menu(self) -> None:
        bar = self.menuBar()

        sess_menu = bar.addMenu("&Session")

        # Project-level actions (top of Session menu — most-used first).
        act_open_project = QAction("Open Project…", self)
        act_open_project.setShortcut(QKeySequence("Ctrl+O"))
        act_open_project.triggered.connect(self._open_project_picker)
        sess_menu.addAction(act_open_project)

        act_new_game = QAction("New Game…", self)
        act_new_game.triggered.connect(self._open_new_game_wizard)
        sess_menu.addAction(act_new_game)

        # Populated lazily so the list reflects whatever the registry currently has.
        self.recent_menu = sess_menu.addMenu("Recent Projects")
        self.recent_menu.aboutToShow.connect(self._populate_recent_menu)

        sess_menu.addSeparator()

        act_settings = QAction("Settings… (configure Godot project)", self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(self._open_settings)
        sess_menu.addAction(act_settings)

        act_new = QAction("New session", self)
        act_new.triggered.connect(self._new_session)
        sess_menu.addAction(act_new)

        act_check = QAction("Check LM Studio connection", self)
        act_check.triggered.connect(self._check_connection)
        sess_menu.addAction(act_check)

        sess_menu.addSeparator()
        act_desktop = QAction("Switch to Desktop session…", self)
        act_desktop.triggered.connect(self._switch_to_desktop)
        sess_menu.addAction(act_desktop)

        act_quit = QAction("Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        sess_menu.addAction(act_quit)

        tools_menu = bar.addMenu("&Tools")
        act_grab = QAction("Capture Godot window", self)
        act_grab.triggered.connect(self._capture_godot)
        tools_menu.addAction(act_grab)
        act_active = QAction("Show active window title", self)
        act_active.triggered.connect(self._show_active_window)
        tools_menu.addAction(act_active)

        tools_menu.addSeparator()
        act_elev = QAction("Check elevation status", self)
        act_elev.triggered.connect(self._refresh_elevation)
        tools_menu.addAction(act_elev)
        act_uac = QAction("Relaunch as admin (UAC)…", self)
        act_uac.triggered.connect(self._relaunch_as_admin)
        tools_menu.addAction(act_uac)

        self.failsafe_action = QAction("PyAutoGUI corner-failsafe", self, checkable=True)
        self.failsafe_action.setChecked(False)  # user chose: disabled
        self.failsafe_action.toggled.connect(self._toggle_failsafe)
        tools_menu.addAction(self.failsafe_action)

        tools_menu.addSeparator()
        act_sig = QAction("Scan Signal-Bus map", self)
        act_sig.triggered.connect(self._show_signal_map)
        tools_menu.addAction(act_sig)

        self.approval_action = QAction("Require approval for file writes", self, checkable=True)
        self.approval_action.setChecked(True)  # user chose: approve-by-default
        self.approval_action.toggled.connect(self._toggle_approval)
        tools_menu.addAction(self.approval_action)

        self.selfheal_action = QAction("Self-Healing (auto-fix Godot errors)", self, checkable=True)
        self.selfheal_action.setChecked(self.orch.self_heal_enabled)
        self.selfheal_action.toggled.connect(self._toggle_self_heal)
        tools_menu.addAction(self.selfheal_action)

        # ---- Vault menu ----
        vault_menu = bar.addMenu("&Vault")
        act_reindex = QAction("Reindex project (RAG)", self)
        act_reindex.setShortcut(QKeySequence("Ctrl+R"))
        act_reindex.triggered.connect(self._reindex_vault)
        vault_menu.addAction(act_reindex)
        act_vstats = QAction("Vault stats", self)
        act_vstats.triggered.connect(self._show_vault_stats)
        vault_menu.addAction(act_vstats)

        # ---- Phoenix menu (self-upgrade) ----
        phoenix_menu = bar.addMenu("&Phoenix")
        self.regenerate_action = QAction("REGENERATE  (continuous self-upgrade)", self, checkable=True)
        self.regenerate_action.setChecked(False)
        self.regenerate_action.toggled.connect(self._toggle_regenerate)
        phoenix_menu.addAction(self.regenerate_action)
        phoenix_menu.addSeparator()
        act_dry = QAction("Run dry-run evolution (safe, synthetic)", self)
        act_dry.triggered.connect(lambda: self._start_evolution("synthetic", dry_run=True))
        phoenix_menu.addAction(act_dry)
        act_llm = QAction("Run one LLM evolution (approve-before-swap)", self)
        act_llm.triggered.connect(lambda: self._start_evolution("llm", dry_run=False))
        phoenix_menu.addAction(act_llm)
        phoenix_menu.addSeparator()
        act_logs = QAction("Evolution logs…", self)
        act_logs.triggered.connect(self._show_evolution_logs)
        phoenix_menu.addAction(act_logs)

        help_menu = bar.addMenu("&Help")
        act_guide = QAction("Quick Start Guide", self)
        act_guide.setShortcut(QKeySequence("F1"))
        act_guide.triggered.connect(self._show_guide)
        help_menu.addAction(act_guide)
        act_diag = QAction("Save diagnostic bundle…", self)
        act_diag.setToolTip("Bundle redacted config, recent logs, and a hardware snapshot into a "
                            "single zip — useful for support / bug reports.")
        act_diag.triggered.connect(self._save_diagnostic_bundle)
        help_menu.addAction(act_diag)
        act_about = QAction("About", self)
        act_about.triggered.connect(self._about)
        help_menu.addAction(act_about)

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self._new_session)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self.input.setFocus())
        QShortcut(QKeySequence("Esc"), self, activated=self._on_escape)
        # Emergency stop — kills every running worker no matter the UI state.
        # Works even when modal dialogs are blocking (Qt routes the shortcut to
        # the parent window). Last-resort safety button.
        emergency = QShortcut(QKeySequence("Ctrl+Alt+K"), self, activated=self._emergency_stop)
        emergency.setContext(Qt.ShortcutContext.ApplicationShortcut)

    def _on_escape(self) -> None:
        if not self.send_btn.isEnabled():  # agent running
            self.orch.cancel()

    def _emergency_stop(self) -> None:
        """Ctrl+Alt+K — kill every running worker and unblock all gates.

        Use cases:
          * Agent hung in a tool call that won't respond to Stop
          * Steam upload going wrong (wrong content root, accidentally hit Upload)
          * Approval / question dialog stuck waiting on a worker that already died
          * Evolution worker in a tight loop

        We never throw from here. The whole point is to be the user's last
        resort — even when other code paths are misbehaving, this should
        succeed.
        """
        log = logger
        actions: list[str] = []
        # 1. Cancel the agent worker (sets isInterruptionRequested + releases gates).
        try:
            self.orch.cancel()
            actions.append("agent cancelled")
        except Exception as exc:  # noqa: BLE001
            log.exception("emergency: agent cancel raised: %s", exc)

        # 2. Stop the Steam workers if any.
        try:
            if (self.steam_panel._upload_worker
                    and self.steam_panel._upload_worker.isRunning()):
                self.steam_panel._upload_worker.requestInterruption()
                actions.append("steam upload aborted")
            if (self.steam_panel._preview_worker
                    and self.steam_panel._preview_worker.isRunning()):
                self.steam_panel._preview_worker.requestInterruption()
                actions.append("steam preview aborted")
        except Exception as exc:  # noqa: BLE001
            log.exception("emergency: steam stop raised: %s", exc)

        # 3. Stop the evolution worker if any.
        try:
            evo = getattr(self.orch, "_evo_worker", None)
            if evo and evo.isRunning():
                evo.requestInterruption()
                actions.append("evolution aborted")
        except Exception as exc:  # noqa: BLE001
            log.exception("emergency: evolution stop raised: %s", exc)

        # 4. Release any blocked approval/question gates so workers can exit.
        try:
            self.orch.gate.cancel_all()
            self.orch.questions.cancel_all()
            actions.append("gates cleared")
        except Exception as exc:  # noqa: BLE001
            log.exception("emergency: gate cancel raised: %s", exc)

        # 5. Reset HUD so the status bar stops claiming we're busy.
        try:
            self.hud.reset()
        except Exception:  # noqa: BLE001
            pass

        # 6. Visible toast + chat note so the user knows what happened.
        msg = "🛑 Emergency stop — " + (", ".join(actions) if actions else "nothing was running")
        self.terminal.append("warn", msg)
        self.chat_panel.add_system(msg)
        self.statusBar().showMessage("Emergency stop fired (Ctrl+Alt+K)", 4000)

    def _show_welcome(self) -> None:
        self.chat_panel.add_system(
            "Welcome to **Outlaw CodeMaker**. Point me at a Godot project, press "
            "**Ctrl+R** to index it into the Vault, then tell me what to build. "
            "Every file change shows a diff you approve with **Enter**. "
            "Press **F1** for the full guide."
        )

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self.orch.stage_chunk.connect(self.thought_chamber.on_stage_chunk)
        self.orch.stage_done.connect(self.thought_chamber.on_stage_done)
        self.orch.tool_invoked.connect(self._on_tool_invoked)
        self.orch.tool_result.connect(self._on_tool_result)
        self.orch.final_answer.connect(self._on_final_answer)
        self.orch.failed.connect(self._on_failed)
        self.orch.log.connect(self.terminal.append)
        self.orch.busy_changed.connect(self._on_busy_changed)
        self.orch.connection_changed.connect(self._on_connection_changed)
        self.orch.advice_ready.connect(self._on_advice_ready)
        self.orch.progress.connect(self._on_progress)
        self.orch.session_changed.connect(self._on_session_changed)
        self.orch.review_requested.connect(self._on_review_requested)
        self.orch.godot_error.connect(self._on_godot_error)
        self.orch.vault_reindexed.connect(self._on_vault_reindexed)
        self.orch.evolution_status.connect(self._on_evolution_status)
        self.orch.evolution_candidate.connect(self._on_evolution_candidate)
        self.orch.evolution_failed.connect(self._on_evolution_failed)
        self.orch.evolution_done.connect(self._on_evolution_done)
        self.orch.workspace_changed.connect(self._on_workspace_changed)
        self.orch.question_requested.connect(self._on_question_requested)
        self.orch.batch_question_requested.connect(self._on_batch_question_requested)
        self.orch.roadmap_updated.connect(self._on_roadmap_updated)
        self.orch.description_updated.connect(self._on_description_updated)
        self.orch.task_paused.connect(self._on_task_paused)
        self.orch.resumable_changed.connect(self._on_resumable_changed)
        # Refresh the VRAM mode badge immediately on a settings change / new task.
        self.orch.vram_mode_changed.connect(lambda _: self._refresh_hardware())
        # LM Studio drop → banner; come-back → green note + auto-resume.
        self.orch.connection_lost.connect(self._on_connection_lost)
        self.orch.connection_restored.connect(self._on_connection_restored)

        self.workspace_view.file_selected.connect(self._on_file_selected)

        self.session_browser.session_open.connect(self._on_session_open)
        self.session_browser.session_new.connect(self._new_session)
        self.session_browser.session_delete.connect(self._on_session_delete)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.chat_panel.add_user(text)
        self.thought_chamber.reset()
        self.hud.reset()
        # Stash for the live "WORKING ON" headline.
        self._current_task_summary = text
        self.orch.submit_task(text)

    @pyqtSlot(bool)
    def _on_busy_changed(self, busy: bool) -> None:
        self.send_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self.input.setDisabled(busy)
        # Live headline — switches to the current task while busy so a glance
        # at the top tells you what the agent is doing without scanning chat.
        if busy and self._current_task_summary:
            summary = self._current_task_summary
            if len(summary) > 96:
                summary = summary[:95] + "…"
            from html import escape as _esc
            self.tagline_label.setText(
                f'<span style="color:#f4d27a;font-weight:600;">▶ WORKING ON:</span> '
                f'<span style="color:#dde3ea;">{_esc(summary)}</span>'
            )
        else:
            self.tagline_label.setText(self._idle_tagline)
        # Failsafe is OFF — make Stop visually unmistakable while the agent runs.
        self.stop_btn.setObjectName("DangerButton" if busy else "")
        self.stop_btn.setStyleSheet("")  # re-evaluate against the global QSS
        self.style().unpolish(self.stop_btn)
        self.style().polish(self.stop_btn)
        if busy:
            self.continue_btn.setVisible(False)  # _on_resumable_changed re-shows it after
        if not busy:
            # Conversation just advanced — keep the History list fresh + correctly ordered.
            self.session_browser.set_current(self.orch.current_session_id)
            self.session_browser.refresh()
            if self.progress_bar.value() >= 100:
                QTimer.singleShot(1500, self._reset_progress_if_idle)

    def _reset_progress_if_idle(self) -> None:
        if self.send_btn.isEnabled():
            self.hud.reset()

    @pyqtSlot(int, str)
    def _on_progress(self, pct: int, label: str) -> None:
        self.hud.update(pct, label)

    @pyqtSlot(str, dict)
    def _on_tool_invoked(self, name: str, args: dict) -> None:
        self.terminal.append("tool", f"{name}({args})")

    @pyqtSlot(str, bool, str)
    def _on_tool_result(self, name: str, ok: bool, payload: str) -> None:
        level = "ok" if ok else "error"
        snippet = payload if len(payload) <= 400 else payload[:400] + "…"
        self.terminal.append(level, f"{name} → {snippet}")
        # If a tool changed the file system, refresh the tree
        if name in {"file_write", "file_delete", "file_mkdir"} and ok:
            self.workspace_view.refresh()

    @pyqtSlot(str)
    def _on_final_answer(self, answer: str) -> None:
        self.chat_panel.add_assistant(answer)
        preview = (answer or "").splitlines()[0] if answer else ""
        if len(preview) > 120:
            preview = preview[:117] + "…"
        self._notify("Agent task complete", preview or "Done.")

    @pyqtSlot(str)
    def _on_failed(self, err: str) -> None:
        self.terminal.append("error", err)
        msg = f"Agent failed: {err}"
        low = err.lower()
        if any(k in low for k in ("memory", "oom", "cuda", "alloc", "500", "internal server")):
            msg += ("\n\nThis looks like the model ran out of VRAM. In LM Studio, lower the "
                    "model's **context length** (e.g. to 4096) or reduce GPU layers a little to "
                    "free VRAM, then click ▸ Continue.")
        self.chat_panel.add_system(msg)
        self.hud.reset()
        # The worker saved a checkpoint on error — offer to resume.
        self.continue_btn.setVisible(self.orch.has_resumable())

    def _on_file_selected(self, rel_path: str) -> None:
        self.input.setText(f"Open and explain {rel_path}")
        self.input.setFocus()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    @pyqtSlot(int, str)
    def _on_session_changed(self, session_id: int, title: str) -> None:
        self.session_label.setText(f"session: {title}")
        self.chat_panel.set_subtitle(title)
        self.session_browser.set_current(session_id)
        self.session_browser.refresh()
        # A resume point is per-session — reflect it on the Continue button.
        self.continue_btn.setVisible(self.orch.has_resumable() and self.send_btn.isEnabled())

    def _on_session_open(self, session_id: int) -> None:
        if not self.send_btn.isEnabled():
            QMessageBox.information(
                self, "Agent busy", "Stop the current task before switching sessions."
            )
            return
        messages = self.orch.open_session(session_id)
        self.chat_panel.render_history(messages)
        self.thought_chamber.reset()
        self.hud.reset()
        self.terminal.append("info", f"Opened session #{session_id} — {len(messages)} message(s).")

    def _on_session_delete(self, session_id: int) -> None:
        confirm = QMessageBox.question(
            self, "Delete session",
            "Delete this conversation permanently? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        was_current = session_id == self.orch.current_session_id
        self.orch.delete_session(session_id)  # may auto-start a new session if current
        if was_current:
            self.chat_panel.clear()
            self.thought_chamber.reset()
        self.session_browser.set_current(self.orch.current_session_id)
        self.session_browser.refresh()
        self.terminal.append("info", f"Deleted session #{session_id}.")

    # ------------------------------------------------------------------
    # Connection / metadata
    # ------------------------------------------------------------------

    def _check_connection(self) -> None:
        """Manual trigger from the menu — just kicks the async probe."""
        self.orch.refresh_connection_async()

    @pyqtSlot(bool, str)
    def _on_connection_changed(self, ok: bool, msg: str) -> None:
        from .styles import dot
        if ok:
            self.conn_label.setText(f'{dot("ok")} LM Studio &nbsp;<span style="color:#838c9c;">{msg}</span>')
            self.model_label.setText(f"model: {self.orch.client.active_model}")
        else:
            self.conn_label.setText(f'{dot("err")} LM Studio &nbsp;<span style="color:#838c9c;">offline</span>')
            self.model_label.setText("model: —")
        # Only log on a state *change* so we don't spam the terminal every 5s.
        if ok != self._last_conn_ok:
            self.terminal.append("ok" if ok else "warn",
                                 f"LM Studio {'connected' if ok else 'offline'} — {msg}")
            self._last_conn_ok = ok
        # If a guided-session task was queued during startup, fire it as soon
        # as the server is reachable.
        if ok and self._pending_kickoff:
            QTimer.singleShot(800, self._try_fire_kickoff)

    def _new_session(self) -> None:
        if not self.send_btn.isEnabled():
            QMessageBox.information(self, "Agent busy", "Stop the current task first.")
            return
        self.orch.new_session()  # emits session_changed → refreshes browser + labels
        self.chat_panel.clear()
        self.thought_chamber.reset()
        self.hud.reset()
        self.terminal.append("info", "Started new session.")

    def _switch_to_desktop(self) -> None:
        """Phase 14a: leave the Dev session and boot into the Desktop session.

        Mirrors the desktop shell's switch-to-Dev: write the session choice plus
        a one-shot ``honor-once`` marker that the greeter reads, then quit — the
        dev X-session ends and .xinitrc restarts straight into the Desktop.
        """
        from pathlib import Path
        from PyQt6.QtWidgets import QApplication

        reply = QMessageBox.question(
            self,
            "Switch to Desktop session",
            "Close Outlaw CodeMaker and switch to the Desktop session?\n\n"
            "The dev session will exit and the desktop will start up.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            home = Path.home()
            (home / ".outlaw-session").write_text("desktop\n", encoding="utf-8")
            (home / ".outlaw-session.honor-once").write_text("", encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Switch failed", f"Couldn't write the session file:\n{exc}")
            return
        # Quitting ends the dev X-session; the greeter, seeing honor-once, goes
        # straight to the Desktop on the restart.
        QApplication.quit()

    # ------------------------------------------------------------------
    # Settings / workspace
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        if not self.send_btn.isEnabled():
            QMessageBox.information(self, "Agent busy", "Stop the current task before changing settings.")
            return
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.config, self)
        if dlg.exec() and dlg.result_config:
            try:
                self.orch.apply_settings(dlg.result_config)
                self.config = dlg.result_config
                QMessageBox.information(
                    self, "Settings saved",
                    "Applied. If you changed the project folder, press Ctrl+R to index it "
                    "into the Vault so the agent knows your code.",
                )
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Couldn't apply settings", str(exc))

    @pyqtSlot(str)
    def _on_workspace_changed(self, ws: str) -> None:
        self.workspace_view.root = Path(ws)
        self.workspace_view.refresh()
        self.roadmap_view.refresh()  # roadmap is per-project
        self.description_view.refresh()  # design sheet is per-project
        self.snapshots_view.refresh()  # snapshots are per-project
        self.steam_panel.set_workspace(ws)  # Steam config is per-project too
        self.workspace_label.setText(f"workspace: {ws}")
        self.orch.refresh_connection_async()
        self.terminal.append("ok", f"Project folder set to {ws}")
        # Keep the projects registry in sync with whatever the orchestrator
        # is pointed at now (registers a new path or just bumps the timestamp).
        self._register_current_workspace()

    def _maybe_first_run(self) -> None:
        """Post-launch setup: show the beginner guide once and register the current project.

        The Project Picker (run from ``main._resolve_startup`` *before* this window
        was built) already handled "no project picked yet" — so we don't need to
        prompt for a folder here. We just keep the guide dialog for newcomers and
        make sure the registry knows about the current workspace.
        """
        from pathlib import Path
        marker = Path(self.config["database"]["path"]).parent / ".onboarded"
        if not marker.exists():
            from .guide import GuideDialog
            GuideDialog(self.orch.client.active_model, self).exec()
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("1", encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass

        self._register_current_workspace()

    # ------------------------------------------------------------------
    # Project picker / wizard / recent menu
    # ------------------------------------------------------------------

    def _register_current_workspace(self) -> None:
        """Ensure the live workspace exists in the projects registry and bump its
        last-opened timestamp. No-op when no store was wired in.
        """
        if not self.projects_store:
            return
        ws = (self.config.get("agent") or {}).get("workspace_root") or ""
        if not ws:
            return
        existing = self.projects_store.get_by_path(ws)
        if existing:
            self.projects_store.touch(ws)
            return
        name = Path(ws).name or "Untitled Project"
        try:
            from vision.godot_project import read_outlaw_metadata, read_project_info
            meta = read_outlaw_metadata(ws) or {}
            if meta.get("name"):
                name = meta["name"]
            else:
                for line in read_project_info(ws).splitlines():
                    if line.startswith("Godot project:"):
                        derived = line.split(":", 1)[1].strip()
                        if derived and derived != "(unnamed)":
                            name = derived
                        break
            self.projects_store.register(
                name=name,
                path=ws,
                dimension=meta.get("dimension"),
                genre=meta.get("genre"),
                steam_app_id=meta.get("steam_app_id"),
                target_platforms=meta.get("target_platforms"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not register workspace %s: %s", ws, exc)

    def _open_project_picker(self) -> None:
        if not self.send_btn.isEnabled():
            QMessageBox.information(
                self, "Agent busy", "Stop the current task before switching projects.",
            )
            return
        if not self.projects_store:
            return
        from .project_picker import ProjectPicker
        picker = ProjectPicker(self.projects_store, parent=self, allow_cancel=True)
        if not picker.exec():
            return
        if picker.chosen_path:
            self._switch_to_project(picker.chosen_path)
        elif picker.want_new_game:
            self._open_new_game_wizard()

    def _open_new_game_wizard(self) -> None:
        if not self.send_btn.isEnabled():
            QMessageBox.information(
                self, "Agent busy", "Stop the current task before starting a new game.",
            )
            return
        if not self.projects_store:
            return
        from .new_game_wizard import NewGameWizard, build_guided_kickoff_task
        paths = self.config.get("paths") or {}
        default_parent = Path(paths.get("default_games_dir") or (Path.home() / "Godot Games"))
        wizard = NewGameWizard(default_parent_dir=default_parent, parent=self)
        if not wizard.exec() or wizard.result is None:
            return
        result = wizard.result
        self.projects_store.register(
            name=result.name,
            path=str(result.project_path),
            dimension=result.dimension,
            genre=result.genre or None,
        )
        self._switch_to_project(str(result.project_path).replace("\\", "/"))
        if result.start_guided_session:
            self.queue_kickoff_task(build_guided_kickoff_task(result))

    def _switch_to_project(self, path: str) -> None:
        """Re-point the orchestrator at a different Godot project. Reuses the
        existing ``apply_settings`` path so workspace-dependent components
        (FileController, Vault, SignalMapper, watchers) all get rebuilt cleanly.
        """
        if path == (self.config.get("agent") or {}).get("workspace_root"):
            return  # already there
        new_cfg = copy.deepcopy(self.config)
        new_cfg.setdefault("agent", {})["workspace_root"] = path
        try:
            self.orch.apply_settings(new_cfg)
            self.config = new_cfg
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Couldn't switch project", str(exc))
            return
        self.chat_panel.add_system(f"Switched to project: **{path}**")

    def _populate_recent_menu(self) -> None:
        """Rebuild the Recent Projects submenu from the registry. Triggered
        every time the menu is about to open so it never goes stale.
        """
        self.recent_menu.clear()
        if not self.projects_store:
            empty = QAction("(no project registry)", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        projects = self.projects_store.list_recent(limit=10)
        if not projects:
            empty = QAction("(no recent projects)", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        current = ((self.config.get("agent") or {}).get("workspace_root") or "").replace("\\", "/")
        for proj in projects:
            label = proj.name + ("  ✓ (current)" if proj.path == current else "")
            act = QAction(label, self)
            act.setToolTip(proj.path)
            act.setEnabled(proj.path != current)
            # Default-arg trick to bind the path at definition time, not call time.
            act.triggered.connect(lambda checked=False, p=proj.path: self._switch_to_project(p))
            self.recent_menu.addAction(act)

    def queue_kickoff_task(self, task: str) -> None:
        """Stash a task to auto-submit when LM Studio comes online and the agent
        is idle. Used by ``main.py`` to start a Guided design session right after
        the wizard creates a project.
        """
        self._pending_kickoff = task
        self.terminal.append(
            "info", "Design session queued — will start when LM Studio is connected.",
        )
        if self.orch.online is True:
            QTimer.singleShot(800, self._try_fire_kickoff)

    def _try_fire_kickoff(self) -> None:
        if not self._pending_kickoff:
            return
        if self.orch.online is not True:
            return
        if not self.send_btn.isEnabled():
            # Agent is mid-task — try again after it idles.
            QTimer.singleShot(2000, self._try_fire_kickoff)
            return
        task = self._pending_kickoff
        self._pending_kickoff = None
        self.chat_panel.add_system("✨ Starting guided design session…")
        self.orch.submit_task(task)

    def _capture_godot(self) -> None:
        result = self.orch.godot.capture(save_path=None)
        QMessageBox.information(self, "Godot capture", result)

    def _show_active_window(self) -> None:
        title = self.orch.godot.active_window_title()
        QMessageBox.information(self, "Active window", title)

    def _about(self) -> None:
        QMessageBox.information(
            self,
            "Outlaw CodeMaker",
            "Outlaw CodeMaker\n\n"
            "An autonomous coding agent for Godot, powered by LM Studio.\n"
            "Plan → Review → Execute. Sees your screen. Writes your code.",
        )

    # ------------------------------------------------------------------
    # Diff approval gate
    # ------------------------------------------------------------------

    @pyqtSlot(str, str, str, str, object)
    def _on_review_requested(self, path: str, old: str, new: str, kind: str, ticket) -> None:
        """The agent (worker thread) is blocked waiting on this decision."""
        from pathlib import Path
        from .diff_dialog import DiffDialog
        try:
            abs_path = str(self.orch.file_controller._resolve(path))
        except Exception:  # noqa: BLE001
            abs_path = str(Path(self.orch.file_controller.root) / path)
        dlg = DiffDialog(path, old, new, kind, self, abs_path=abs_path)
        dlg.exec()
        approved = dlg.approved
        self.orch.gate.resolve(ticket, approved)
        self.terminal.append("ok" if approved else "warn",
                              f"{'Approved' if approved else 'Denied'} change to {path}")

    def _toggle_approval(self, enabled: bool) -> None:
        self.orch.gate.enabled = enabled
        self.terminal.append("info",
                             f"Write approval {'ON — diffs require approval' if enabled else 'OFF — full autonomy'}")

    # ------------------------------------------------------------------
    # Agent questions / roadmap / continue
    # ------------------------------------------------------------------

    @pyqtSlot(dict, object)
    def _on_question_requested(self, spec: dict, ticket) -> None:
        """The agent (worker thread) is blocked waiting for the user's answer."""
        from .question_dialog import QuestionDialog
        dlg = QuestionDialog(spec, self)
        dlg.exec()
        self.orch.questions.resolve(ticket, dlg.answer)
        self.chat_panel.add_system(f"You answered: {dlg.answer}")

    @pyqtSlot(list, object)
    def _on_batch_question_requested(self, specs: list, ticket) -> None:
        """All of the agent's questions at once; on Submit it continues automatically."""
        from .question_dialog import MultiQuestionDialog
        dlg = MultiQuestionDialog(specs, self)
        dlg.exec()
        answers = dlg.answers or ["(skipped)"] * len(specs)
        self.orch.questions.resolve_batch(ticket, answers)
        lines = "\n".join(
            f"• {s.get('question', '?')} → {a}" for s, a in zip(specs, answers)
        )
        self.chat_panel.add_system(f"You answered {len(answers)} question(s):\n{lines}")

    @pyqtSlot(dict)
    def _on_roadmap_updated(self, data: dict) -> None:
        self.roadmap_view.show_data(data)
        # Nudge the user to the Roadmap tab the first time one appears.
        self.left_tabs.setTabText(2, "Roadmap •")

    @pyqtSlot(dict)
    def _on_description_updated(self, data: dict) -> None:
        # Phase 14b: the agent filled/updated the design sheet — reload it from
        # the store and flag the Design tab so the user notices.
        self.description_view.show_data(data)
        self.left_tabs.setTabText(3, "Design •")

    @pyqtSlot(str)
    def _on_task_paused(self, hint: str) -> None:
        self.chat_panel.add_system(f"⏸ {hint}")
        self.continue_btn.setVisible(True)
        self.terminal.append("info", "Task paused — resume anytime with Continue.")
        self._notify("Agent paused", hint, urgency="warn")

    @pyqtSlot(str)
    def _on_connection_lost(self, reason: str) -> None:
        """Worker hit a transient LM Studio error — task is parked, orchestrator
        is polling. Tell the user what's happening so they don't think we died."""
        short = (reason or "").splitlines()[-1][:160] if reason else "(no detail)"
        self.chat_panel.add_system(
            "⚠ LM Studio dropped — Outlaw is reconnecting and will pick the task back up "
            f"as soon as the server's back.<br><span style='color:#838c9c;'>{short}</span><br>"
            "Hit <b>Stop</b> if you'd rather cancel waiting."
        )
        self.terminal.append("warn", f"Reconnect-mode: {short}")
        self.continue_btn.setVisible(False)  # auto-continue will handle it
        self.hud.update(0, "Waiting for LM Studio…")

    @pyqtSlot()
    def _on_connection_restored(self) -> None:
        self.chat_panel.add_system("✓ LM Studio is back. Resuming the paused task…")
        self.terminal.append("ok", "LM Studio reconnected — auto-resuming.")

    # ------------------------------------------------------------------
    # Desktop notifications
    # ------------------------------------------------------------------

    def _build_notifier(self) -> None:
        """Create a QSystemTrayIcon for OS notifications. Falls back gracefully
        when the desktop has no tray (e.g., minimal WMs); the chat-system
        message still fires."""
        from PyQt6.QtWidgets import QSystemTrayIcon
        from .styles import make_app_icon
        self._tray = None
        try:
            if QSystemTrayIcon.isSystemTrayAvailable():
                self._tray = QSystemTrayIcon(make_app_icon(), self)
                self._tray.setToolTip("Outlaw CodeMaker")
                self._tray.show()
        except Exception as exc:  # noqa: BLE001
            logger.warning("System tray unavailable: %s", exc)
            self._tray = None

    def _notify(self, title: str, message: str, urgency: str = "info") -> None:
        """Fire a desktop notification. Best-effort: degrades to chat if tray missing."""
        if self.window().isActiveWindow():
            # User is already looking at the app — chat panel is enough.
            return
        try:
            from PyQt6.QtWidgets import QSystemTrayIcon
            if self._tray:
                icon = {
                    "info":  QSystemTrayIcon.MessageIcon.Information,
                    "warn":  QSystemTrayIcon.MessageIcon.Warning,
                    "error": QSystemTrayIcon.MessageIcon.Critical,
                }.get(urgency, QSystemTrayIcon.MessageIcon.Information)
                self._tray.showMessage(title, message, icon, 6000)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Notification failed: %s", exc)

    @pyqtSlot(bool)
    def _on_resumable_changed(self, resumable: bool) -> None:
        self.continue_btn.setVisible(resumable and self.send_btn.isEnabled())

    def _on_continue(self) -> None:
        self.continue_btn.setVisible(False)
        self.thought_chamber.reset()
        self.hud.reset()
        self.orch.continue_task()

    def _on_left_tab_changed(self, index: int) -> None:
        if index == 1:
            self.session_browser.refresh()
        elif index == 2:
            self.roadmap_view.refresh()
            self.left_tabs.setTabText(2, "Roadmap")  # clear the "new" dot
        elif index == 3:
            # Design sheet — reload the per-project sheet each visit.
            self.description_view.refresh()
            self.left_tabs.setTabText(3, "Design")  # clear the "new" dot
        elif index == 4:
            # Snapshots tab — re-read folder each visit so new captures show up
            # without needing the Refresh button.
            self.snapshots_view.refresh()
        elif index == 5:
            # Re-check steamcmd/login state every time the user opens the tab
            # so cached login expiry is noticed without a manual refresh click.
            self.steam_panel._refresh_status()

    # ------------------------------------------------------------------
    # Vault / Signals / Self-Heal
    # ------------------------------------------------------------------

    def _reindex_vault(self) -> None:
        self.orch.reindex_vault_async()

    @pyqtSlot(int, str)
    def _on_vault_reindexed(self, n: int, msg: str) -> None:
        self.terminal.append("ok", msg)
        self.statusBar().showMessage(msg, 5000)
        self._notify("Vault reindex complete", msg)

    def _show_vault_stats(self) -> None:
        s = self.orch.vault.stats()
        QMessageBox.information(
            self, "Vault stats",
            f"Indexed chunks: {s['chunks']}\nFiles: {s['files']}\n"
            f"Last indexed: {s['indexed_at'] or 'never — run Reindex (Ctrl+R)'}",
        )

    def _show_signal_map(self) -> None:
        smap = self.orch.signal_mapper.scan()
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Signal-Bus Map")
        dlg.setText(smap.summary()[:3000] or "(nothing found)")
        dlg.setStyleSheet("QLabel{font-family:Consolas,monospace;min-width:560px;}")
        dlg.exec()

    def _toggle_self_heal(self, enabled: bool) -> None:
        self.orch.set_self_heal(enabled)

    @pyqtSlot(str, str)
    def _on_godot_error(self, source: str, text: str) -> None:
        self.chat_panel.add_system(f"⚠ Godot error detected ({source}) — self-healing engaged.")
        self.terminal.append("error", f"[{source}] {text[:200]}")

    # ------------------------------------------------------------------
    # Hardware awareness
    # ------------------------------------------------------------------

    def _refresh_hardware(self) -> None:
        line = self.orch.hardware_line()
        self.hardware_label.setText(line)
        crit = self.orch.hardware.vram_critical()
        self.hardware_label.setStyleSheet("color:#e0625e;" if crit else "color:#838c9c;")
        # VRAM saver mode badge — color-coded so a glance at the status bar
        # tells the user whether the agent is currently shrinking its context.
        try:
            mode = self.orch.vram_saver.current_mode().label
        except Exception:  # noqa: BLE001
            mode = "—"
        pref = getattr(self.orch.vram_saver, "pref", "auto")
        suffix = f" ({pref})" if pref not in {"auto", "off"} else ""
        self.vram_mode_label.setText(f"VRAM: {mode}{suffix}")
        color = {
            "Full":    "#5cc97a",   # ok
            "Lean":    "#e8a84c",   # warn
            "Minimal": "#e0625e",   # err
        }.get(mode, "#838c9c")
        self.vram_mode_label.setStyleSheet(f"color:{color};")

    # ------------------------------------------------------------------
    # Phoenix Protocol (self-upgrade)
    # ------------------------------------------------------------------

    def _start_evolution(self, mode: str, dry_run: bool) -> None:
        if mode == "llm" and self.orch.online is not True:
            QMessageBox.information(
                self, "LM Studio offline",
                "An LLM evolution needs LM Studio connected. The synthetic dry-run works offline.",
            )
            return
        self._evo_pending = True
        self.orch.start_evolution(mode, dry_run)

    def _toggle_regenerate(self, on: bool) -> None:
        self._regenerate_on = on
        if on:
            self.terminal.append("ok", "REGENERATE armed — continuous self-upgrade (approve-before-swap).")
            self.phoenix_label.setText('<span style="color:#f4d27a;">🔥 REGENERATE</span>')
            self._start_evolution("llm", dry_run=False)
        else:
            self.terminal.append("info", "REGENERATE disarmed.")
            self.phoenix_label.setText("")

    @pyqtSlot(str)
    def _on_evolution_status(self, msg: str) -> None:
        tag = "🔥 REGENERATE · " if self._regenerate_on else "🔥 "
        self.phoenix_label.setText(f'<span style="color:#f4d27a;">{tag}{msg}</span>')
        self.statusBar().showMessage(f"Phoenix: {msg}", 4000)

    @pyqtSlot(dict)
    def _on_evolution_candidate(self, cand: dict) -> None:
        self._evo_pending = False
        from .evolution_dialogs import EvolutionReviewDialog
        dlg = EvolutionReviewDialog(cand, self.orch.evolution, self)
        dlg.exec()
        if dlg.approved and not cand.get("dry_run"):
            self.orch.apply_candidate(cand)
            self.chat_panel.add_system(
                "🔥 Evolution approved and hot-swapped. Restarting to load the new build…"
            )
            QTimer.singleShot(800, self._restart_app)
            return
        # Discarded (or dry-run): re-arm if continuous mode is on.
        self.terminal.append("info", "Evolution candidate "
                             + ("logged (dry-run)." if cand.get("dry_run") else "discarded."))
        self._maybe_rearm()

    @pyqtSlot(str)
    def _on_evolution_failed(self, msg: str) -> None:
        self._evo_pending = False
        self.terminal.append("error", "Evolution: " + msg.splitlines()[0])
        self.chat_panel.add_system("Evolution attempt failed validation — live files untouched.")
        self._maybe_rearm()

    @pyqtSlot(bool)
    def _on_evolution_done(self, ok: bool) -> None:
        if not self._regenerate_on and not self._evo_pending:
            self.phoenix_label.setText("")

    def _maybe_rearm(self) -> None:
        if self._regenerate_on:
            self.terminal.append("info", "REGENERATE: next cycle in 45s…")
            QTimer.singleShot(45_000, lambda: self._start_evolution("llm", dry_run=False)
                              if self._regenerate_on else None)
        else:
            self.phoenix_label.setText("")

    def _show_evolution_logs(self) -> None:
        from .evolution_dialogs import EvolutionLogDialog
        EvolutionLogDialog(self.orch.list_evolution(), self).exec()

    def _restart_app(self) -> None:
        import subprocess
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        self.orch.shutdown()
        try:
            subprocess.Popen([sys.executable, "boot_restore.py"], cwd=str(root))
        except Exception as exc:  # noqa: BLE001
            self.terminal.append("error", f"Restart failed: {exc}")
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    def _show_guide(self) -> None:
        from .guide import GuideDialog
        GuideDialog(self.orch.client.active_model, self).exec()

    def _save_diagnostic_bundle(self) -> None:
        """Help → Save diagnostic bundle… — produce a zip the user can attach
        to a bug report. Bundles redacted config, recent logs, and a hardware
        snapshot. NEVER includes project files or databases."""
        from PyQt6.QtWidgets import QFileDialog
        from core.diagnostics import build_bundle, default_bundle_name
        default_dir = str(Path.home() / "Downloads")
        default_path = str(Path(default_dir) / default_bundle_name())
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save diagnostic bundle", default_path, "Zip files (*.zip)",
        )
        if not chosen:
            return
        try:
            hardware = self.orch.hardware.snapshot()
        except Exception:  # noqa: BLE001
            hardware = {}
        try:
            from core.version import VERSION as _OUTLAW_VERSION  # type: ignore
        except Exception:  # noqa: BLE001
            _OUTLAW_VERSION = None  # version module is optional
        try:
            path = build_bundle(
                chosen,
                config=self.config,
                hardware_snapshot=hardware,
                active_model=self.orch.client.active_model,
                version=_OUTLAW_VERSION,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Diagnostic bundle failed",
                                 f"Couldn't write the bundle:\n\n{exc}")
            return
        QMessageBox.information(
            self, "Diagnostic bundle saved",
            f"Saved to:\n{path}\n\nSafe to attach to a bug report — no project files "
            "or databases are included.",
        )
        self.terminal.append("ok", f"Diagnostic bundle saved: {path}")

    # ------------------------------------------------------------------
    # Elevation
    # ------------------------------------------------------------------

    def _refresh_elevation(self) -> None:
        from .styles import dot
        info = self.orch.check_elevation()
        me = info["self_level"]
        admin = " (admin)" if info["is_admin"] else ""
        if not info["godot_found"]:
            self.elev_label.setText(
                f'{dot("muted")} <span style="color:#838c9c;">{me}{admin} · godot not running</span>'
            )
            return
        them = info["godot_level"]
        if info["mismatch"]:
            self.elev_label.setText(
                f'{dot("err")} <span style="color:#e0625e;">UIPI block — {me}{admin} vs godot {them} · relaunch as admin</span>'
            )
            self._offer_uac_relaunch_once(them)
        else:
            self.elev_label.setText(
                f'{dot("ok")} <span style="color:#838c9c;">{me}{admin} · godot {them} · ok</span>'
            )

    _uac_prompt_shown = False

    def _offer_uac_relaunch_once(self, godot_level: str) -> None:
        if self._uac_prompt_shown:
            return
        self._uac_prompt_shown = True
        choice = QMessageBox.question(
            self,
            "Elevation mismatch",
            f"Godot is running at {godot_level} integrity but Outlaw is running at standard.\n\n"
            "Windows will block input (clicks, typing, focus) sent from us to Godot.\n\n"
            "Relaunch Outlaw with admin rights now? (You'll see a UAC prompt.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self._relaunch_as_admin()

    def _relaunch_as_admin(self) -> None:
        ok = self.orch.relaunch_with_uac()
        if ok:
            self.terminal.append("info", "Relaunch initiated — closing this instance.")
            QTimer.singleShot(400, self.close)
        else:
            QMessageBox.warning(
                self,
                "Relaunch failed",
                "Could not relaunch as admin (UAC cancelled or already elevated).",
            )

    def _toggle_failsafe(self, enabled: bool) -> None:
        self.orch.automation.set_failsafe(enabled)
        state = "ON (mouse to corner aborts)" if enabled else "OFF (Stop button only)"
        self.terminal.append("info", f"PyAutoGUI failsafe → {state}")

    # ------------------------------------------------------------------
    # Proactive Advisory Mode
    # ------------------------------------------------------------------

    def _start_proactive_timer(self) -> None:
        self._last_file_count = self.orch.file_controller.file_count()
        interval_ms = max(30, int(self.config["agent"]["proactive_advice_interval_seconds"])) * 1000
        self.advice_timer = QTimer(self)
        self.advice_timer.setInterval(interval_ms)
        self.advice_timer.timeout.connect(self._maybe_offer_advice)
        self.advice_timer.start()

    def _maybe_offer_advice(self) -> None:
        # Skip while the agent is working, while a banner is already up, or offline.
        if not self.send_btn.isEnabled() or self.banner.isVisible():
            return
        if self.orch.online is not True:
            return

        current = self.orch.file_controller.file_count()
        workspace_summary = self.orch.file_controller.list_tree(depth=3)

        prompt = (
            "You are a proactive advisor watching a Godot project. "
            "Given the workspace tree below, suggest ONE small, concrete improvement "
            "the developer might want to make next. Reply with a single sentence "
            "phrased as a question, e.g. 'Want me to ...?'. If nothing stands out, "
            "reply with exactly: NOTHING.\n\n"
            f"## Workspace ({current} files)\n{workspace_summary}"
        )
        # Runs on a background thread; result returns via the advice_ready signal.
        self.orch.request_advice_async(prompt)

    @pyqtSlot(str)
    def _on_advice_ready(self, response: str) -> None:
        response = (response or "").strip()
        if not response or response.upper().startswith("NOTHING"):
            return
        if self.banner.isVisible():
            return
        try:
            self.orch.store.save_proactive_advice(self.orch.memory.current.session_id, response)
        except Exception:  # noqa: BLE001
            pass
        self.banner.show_advice(response)

    def _accept_advice(self, advice: str) -> None:
        if not advice:
            return
        self.chat_panel.add_advice(advice)
        self.input.setText(advice)
        self._on_send()

    def _dismiss_advice(self) -> None:
        self.terminal.append("info", "Advice dismissed.")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Stop timers and background threads so we exit cleanly.
        for timer in (getattr(self, "_conn_timer", None),
                      getattr(self, "_elev_timer", None),
                      getattr(self, "advice_timer", None)):
            if timer:
                timer.stop()
        self.orch.shutdown()
        super().closeEvent(event)
