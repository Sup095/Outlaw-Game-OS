"""Outlaw CodeMaker — entry point.

Wires the Qt application together with the Orchestrator and shows the main
window. Before the orchestrator is built, ``_resolve_startup`` decides which
Godot project to load — pinned in config, most-recently-opened from the
registry, or chosen via the Project Picker (with a New Game wizard branch).

Crash-handles unhandled exceptions so the UI surfaces them instead of dying
silently.
"""

from __future__ import annotations

import hashlib
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


class _SharedErrorLogHandler(logging.Handler):
    """F1 — mirror WARNING+ records into the combined ``~/.outlaw-errors.log`` the
    desktop's "Report a problem" reads, so the DEV SESSION's errors ship in the same
    report. Deduped (never the same line twice, seeded from the file across sessions);
    best-effort — never raises."""

    _path = Path.home() / ".outlaw-errors.log"

    @staticmethod
    def _key(body: str) -> str:
        # #6 — STABLE content key shared with the desktop shell (errorlog.js _key).
        # sha1 of the normalized body so the same logical error dedups across BOTH
        # writers and across restarts. Python's built-in hash() is randomized per
        # process and differs from JS, so it could never match the shell's entries.
        return hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()

    @staticmethod
    def _line_body(line: str) -> str:
        # Recover "source: message" from `[<iso>] <LEVEL> <source>: <message>` —
        # strip the bracketed timestamp AND the single LEVEL token. Must match the
        # shell's _lineBody so seeding from disk dedups against the shell's writes.
        return re.sub(r"^\[[^\]]*\]\s*\S+\s*", "", line).strip()

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._seen: set[str] = set()
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                body = self._line_body(line)
                if body:
                    self._seen.add(self._key(body))
        except Exception:  # noqa: BLE001
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.WARNING:
                return
            msg = " ".join(self.format(record).split())[:600]
            body = "codemaker: " + msg
            key = self._key(body)
            if key in self._seen:
                return
            self._seen.add(key)
            import datetime
            level = "ERROR" if record.levelno >= logging.ERROR else "WARN"
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {level} {body}\n")
        except Exception:  # noqa: BLE001 — logging must never crash the app
            pass


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # F1 — also feed errors/warnings into the shared system error log.
    try:
        h = _SharedErrorLogHandler()
        h.setFormatter(logging.Formatter("%(name)s | %(message)s"))
        logging.getLogger().addHandler(h)
    except Exception:  # noqa: BLE001
        pass
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

    # Build the orchestrator + main window. If anything here throws (AI client,
    # workspace load, a bad config), report it clearly instead of hard-crashing
    # back to the session picker — and log the full traceback so it lands in the
    # error log for diagnosis.
    try:
        orchestrator = Orchestrator(config)
        window = MainWindow(orchestrator, config, projects_store=projects_store)
    except Exception:  # noqa: BLE001
        import traceback
        logging.getLogger("outlaw").exception("Failed to start the workspace window")
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Outlaw CodeMaker — startup error",
            "Something went wrong opening the workspace. It's been written to the "
            "error log.\n\nDetails:\n" + traceback.format_exc()[-1600:],
        )
        return 1
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
