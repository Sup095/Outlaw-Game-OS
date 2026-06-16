"""Outlaw CodeMaker — entry point.

Wires the Qt application together with the Orchestrator and shows the main
window. Before the orchestrator is built, ``_resolve_startup`` decides which
Godot project to load — pinned in config, most-recently-opened from the
registry, or chosen via the Project Picker (with a New Game wizard branch).

Crash-handles unhandled exceptions so the UI surfaces them instead of dying
silently.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.orchestrator import Orchestrator
from db.projects import ProjectsStore
from db.schema import init_database
from ui.main_window import MainWindow
from ui.new_game_wizard import NewGameWizard, build_guided_kickoff_task
from ui.project_picker import ProjectPicker
from ui.styles import apply_dark_theme

try:
    import boot_restore
except Exception:  # noqa: BLE001
    boot_restore = None


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"


# A Windows drive-letter absolute path (e.g. "F:/Godot Games", "C:\\x"). These
# get baked into config.json on the Windows dev box and are meaningless on the
# installed Linux OS — opening one raises PermissionError [Errno 13] 'F:' and
# crashes startup. We rewrite them to home-based defaults at load time.
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_nonportable(value: object) -> bool:
    """True if `value` is a Windows drive path but we're NOT on Windows."""
    return os.name != "nt" and isinstance(value, str) and bool(_WIN_DRIVE_RE.match(value))


def _sanitize_config(config: dict) -> dict:
    """Heal non-portable paths so CodeMaker starts on a fresh Linux install
    instead of crashing on the dev box's 'F:/...' paths. Only paths invalid for
    THIS OS are touched — on Windows nothing changes, so the dev box is unaffected."""
    home = Path.home()
    db_dir = home / ".outlaw-codemaker" / "db"

    db = config.setdefault("database", {})
    if _is_nonportable(db.get("path")) or not db.get("path"):
        db["path"] = str(db_dir / "outlaw.db")
    if _is_nonportable(db.get("vault_path")) or not db.get("vault_path"):
        db["vault_path"] = str(db_dir / "vault.db")

    paths = config.setdefault("paths", {})
    if _is_nonportable(paths.get("default_games_dir")) or not paths.get("default_games_dir"):
        paths["default_games_dir"] = str(home / "Godot Games")

    # workspace_root / godot.log_path point at a specific project; a stale dev-box
    # path here just means "no project open" — clear it so the Project Picker
    # takes over instead of the orchestrator erroring on a missing path.
    agent = config.setdefault("agent", {})
    if _is_nonportable(agent.get("workspace_root")):
        agent["workspace_root"] = ""
    godot = config.setdefault("godot", {})
    if _is_nonportable(godot.get("log_path")):
        godot["log_path"] = ""

    # A Windows tesseract.exe path can't work on Linux; empty = use the system
    # `tesseract` on PATH (what pytesseract defaults to).
    vision = config.setdefault("vision", {})
    tcmd = vision.get("tesseract_cmd")
    if isinstance(tcmd, str) and (tcmd.endswith(".exe") or _is_nonportable(tcmd)):
        vision["tesseract_cmd"] = ""

    # Ensure the DB directory exists so the first open() can't fail either.
    for key in ("path", "vault_path"):
        try:
            Path(db[key]).parent.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
    return config


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return _sanitize_config(json.load(fh))


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Install the diagnostic ring buffer alongside the console handler. It
    # captures the last few hundred records in memory so Help → Save
    # diagnostic bundle has something useful to ship — without us having to
    # tail a log file from disk.
    from core.diagnostics import install_ring_buffer
    install_ring_buffer(capacity=500)


def install_excepthook(app: QApplication) -> None:
    def _hook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.getLogger("outlaw").error("Unhandled exception:\n%s", msg)
        QMessageBox.critical(None, "Outlaw CodeMaker — fatal", msg)
    sys.excepthook = _hook


# ---------------------------------------------------------------------------
# Project-resolution flow (runs BEFORE the orchestrator is built)
# ---------------------------------------------------------------------------


@dataclass
class StartupState:
    """What the resolver hands to the rest of main()."""
    workspace_root: str
    guided_kickoff: str | None = None  # if set, MainWindow auto-submits after launch


def _default_games_dir(config: dict) -> Path:
    """Where the New Game wizard should default for the parent folder."""
    paths = config.get("paths") or {}
    raw = paths.get("default_games_dir") or ""
    if raw:
        return Path(raw)
    # Fall back: <user home>/Godot Games
    return Path.home() / "Godot Games"


def _is_usable_project(path: str | Path) -> bool:
    """A path is usable if it exists; project.godot is preferred but not required."""
    p = Path(path)
    return bool(p) and p.exists()


def _resolve_startup(config: dict, store: ProjectsStore) -> StartupState | None:
    """Decide which project to open at launch. Returns None if user bailed."""
    # 1. Honor an explicit pinned workspace if it still exists.
    pinned = (config.get("agent") or {}).get("workspace_root") or ""
    if pinned and _is_usable_project(pinned):
        return StartupState(workspace_root=str(Path(pinned)).replace("\\", "/"))

    # 2. Otherwise fall back to whatever the user opened last.
    recent = store.most_recent()
    if recent and _is_usable_project(recent.path):
        return StartupState(workspace_root=recent.path)

    # 3. First launch (or nothing usable) — ask via the picker.
    return _run_picker_loop(config, store)


def _run_picker_loop(config: dict, store: ProjectsStore) -> StartupState | None:
    """Loop picker → wizard → back-to-picker until the user picks or cancels."""
    while True:
        picker = ProjectPicker(store, allow_cancel=False)
        if not picker.exec():
            return None
        if picker.chosen_path:
            return StartupState(workspace_root=picker.chosen_path)
        if picker.want_new_game:
            wizard = NewGameWizard(default_parent_dir=_default_games_dir(config))
            if not wizard.exec() or wizard.result is None:
                continue  # user backed out of wizard → re-show picker
            result = wizard.result
            store.register(
                name=result.name,
                path=str(result.project_path),
                dimension=result.dimension,
                genre=result.genre or None,
            )
            kickoff = build_guided_kickoff_task(result) if result.start_guided_session else None
            return StartupState(
                workspace_root=str(result.project_path).replace("\\", "/"),
                guided_kickoff=kickoff,
            )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    configure_logging()
    config = load_config()
    init_database(config["database"]["path"])
    projects_store = ProjectsStore(config["database"]["path"])

    app = QApplication(sys.argv)
    apply_dark_theme(app, config["ui"])
    install_excepthook(app)

    startup = _resolve_startup(config, projects_store)
    if startup is None:
        logging.getLogger("outlaw").info("Startup cancelled by user — exiting.")
        return 0

    # Inject the chosen workspace into the live config dict (in-memory only —
    # we don't rewrite config.json from here; the orchestrator's apply_settings
    # owns that path).
    config.setdefault("agent", {})["workspace_root"] = startup.workspace_root
    projects_store.touch(startup.workspace_root)

    orchestrator = Orchestrator(config)
    window = MainWindow(orchestrator, config, projects_store=projects_store)
    # Phase 14a: open filling the screen (the Dev session is the whole screen).
    # Maximized, not kiosk-fullscreen, so the title bar + taskbar stay reachable.
    window.showMaximized()

    # Tell the boot failsafe we launched healthily (clears the crash counter).
    if boot_restore is not None:
        QTimer.singleShot(3000, boot_restore.record_success)

    # If the wizard picked Guided mode, the kickoff task waits until LM Studio
    # is reachable, then auto-submits. Single-shot to avoid spamming.
    if startup.guided_kickoff:
        window.queue_kickoff_task(startup.guided_kickoff)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
