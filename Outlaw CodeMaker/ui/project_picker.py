"""Project Picker — the first thing you see on launch (when no project is loaded)
or anytime you pick *Session → Open Project*.

Three exits:
    1. picked an existing recent project   → ``chosen_path`` is set
    2. browsed to a folder on disk         → ``chosen_path`` is set
    3. clicked "New Game"                  → ``want_new_game`` is True

The caller (``main_window`` / ``main``) wires those into the orchestrator and,
for "new game", opens the wizard.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from db.projects import Project, ProjectsStore

from .styles import COLORS


# ---------------------------------------------------------------------------
# Row widget — one card per recent project
# ---------------------------------------------------------------------------


class _ProjectCard(QFrame):
    """Pretty card for a single project. Used as the item widget in the list."""

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self.setObjectName("ProjectCard")
        self.setStyleSheet(
            f"QFrame#ProjectCard {{"
            f"  background-color: {COLORS['panel']};"
            f"  border: 1px solid {COLORS['border']};"
            f"  border-radius: 8px;"
            f"}}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        name = QLabel(project.name or "(unnamed project)")
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(11)
        name.setFont(name_font)
        name.setStyleSheet(f"color: {COLORS['accent2']};")
        title_row.addWidget(name, 1)

        when = QLabel(_humanize_time(project.last_opened_at))
        when.setStyleSheet(f"color: {COLORS['muted']}; font-size: 9pt;")
        title_row.addWidget(when, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(title_row)

        path = QLabel(project.path)
        path.setStyleSheet(f"color: {COLORS['muted']}; font-size: 9pt;")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path)

        tags: list[str] = []
        if project.dimension:
            tags.append(project.dimension)
        if project.genre:
            tags.append(project.genre)
        if project.steam_app_id:
            tags.append(f"steam:{project.steam_app_id}")
        if tags:
            tag_row = QLabel("  ·  ".join(tags))
            tag_row.setStyleSheet(f"color: {COLORS['accent']}; font-size: 9pt;")
            layout.addWidget(tag_row)


def _humanize_time(iso: str) -> str:
    """Render an ISO timestamp as a short relative string for the card corner."""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    delta = datetime.now() - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86_400:
        return f"{secs // 3600}h ago"
    if secs < 86_400 * 7:
        return f"{secs // 86_400}d ago"
    return dt.strftime("%b %d")


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class ProjectPicker(QDialog):
    """Modal project picker. Caller reads ``chosen_path`` / ``want_new_game`` after exec()."""

    def __init__(self, store: ProjectsStore, parent=None, allow_cancel: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Outlaw CodeMaker — Choose a project")
        self.resize(680, 520)
        # Phase 14g: the pre-session screen fills the screen too (the Dev session
        # is the whole screen), and offers real setup/exit actions below.
        self.setWindowState(Qt.WindowState.WindowMaximized)

        self.store = store
        self.chosen_path: str | None = None
        self.want_new_game: bool = False
        self._allow_cancel = allow_cancel

        # ----- Layout -----

        intro = QLabel(
            "Pick a Godot game to keep working on, open a folder on disk, "
            "or start a new game from scratch."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {COLORS['muted']};")

        # Phase 14g — set-up / tools row so you're never trapped here: open Godot
        # and LM Studio to set them up, switch back to the Desktop, or quit.
        setup_hint = QLabel(
            "First time? Open Godot and LM Studio below to set them up, then start a new game."
        )
        setup_hint.setWordWrap(True)
        setup_hint.setStyleSheet(f"color: {COLORS['accent']}; font-size: 9pt;")
        godot_btn = QPushButton("🎮  Open Godot")
        godot_btn.setToolTip("Launch Godot — or download & install it first if it's missing.")
        godot_btn.clicked.connect(self._open_godot)
        lm_btn = QPushButton("✦  Open LM Studio")
        lm_btn.setToolTip("Launch LM Studio — or open its download page if it isn't installed yet.")
        lm_btn.clicked.connect(self._open_lmstudio)
        ask_btn = QPushButton("💬  Ask V4rm1nt")
        ask_btn.setToolTip("Chat with the bundled base AI for setup help — no LM Studio needed.")
        ask_btn.clicked.connect(self._ask_v4rmint)
        to_desktop_btn = QPushButton("⌂  Switch to Desktop")
        to_desktop_btn.clicked.connect(self._switch_to_desktop)
        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(self._quit_app)
        setup_row = QHBoxLayout()
        setup_row.addWidget(godot_btn)
        setup_row.addWidget(lm_btn)
        setup_row.addWidget(ask_btn)
        setup_row.addStretch(1)
        setup_row.addWidget(to_desktop_btn)
        setup_row.addWidget(quit_btn)

        recent_label = QLabel("RECENT PROJECTS")
        recent_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-weight: 700; letter-spacing: 1px;"
        )

        self.list = QListWidget()
        self.list.setSpacing(6)
        self.list.itemActivated.connect(self._on_item_activated)
        self.list.itemDoubleClicked.connect(self._on_item_activated)

        # Empty-state placeholder
        self.empty_label = QLabel(
            "No projects yet. Open an existing Godot folder, or start a new game."
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {COLORS['muted']}; padding: 32px;")

        # Action buttons
        new_btn = QPushButton("✨  New Game…")
        new_btn.setObjectName("PrimaryButton")
        new_btn.setMinimumHeight(40)
        new_btn.clicked.connect(self._on_new_game)

        open_btn = QPushButton("📂  Open Existing Folder…")
        open_btn.setMinimumHeight(40)
        open_btn.clicked.connect(self._on_open_folder)

        forget_btn = QPushButton("Forget selected")
        forget_btn.setToolTip("Remove from recent list (does not delete files)")
        forget_btn.clicked.connect(self._on_forget_selected)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setVisible(allow_cancel)

        action_row = QHBoxLayout()
        action_row.addWidget(forget_btn)
        action_row.addStretch(1)
        action_row.addWidget(cancel_btn)
        action_row.addWidget(open_btn)
        action_row.addWidget(new_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(intro)
        layout.addWidget(setup_hint)
        layout.addLayout(setup_row)
        layout.addSpacing(6)
        layout.addWidget(recent_label)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty_label)
        layout.addLayout(action_row)

        self._refresh()

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self.list.clear()
        projects = self.store.list_recent(limit=20)
        if not projects:
            self.list.setVisible(False)
            self.empty_label.setVisible(True)
            return
        self.list.setVisible(True)
        self.empty_label.setVisible(False)
        for proj in projects:
            card = _ProjectCard(proj)
            item = QListWidgetItem()
            item.setSizeHint(card.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, proj.path)
            self.list.addItem(item)
            self.list.setItemWidget(item, card)

    def _selected_path(self) -> str | None:
        item = self.list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        if not self._validate_godot_project(path):
            return
        self.chosen_path = path
        self.accept()

    def _on_open_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose your Godot project folder", str(Path.home())
        )
        if not path:
            return
        path = path.replace("\\", "/")
        if not self._validate_godot_project(path, offer_register_anyway=True):
            return
        self.chosen_path = path
        self.accept()

    def _on_new_game(self) -> None:
        self.want_new_game = True
        self.accept()

    def _on_forget_selected(self) -> None:
        path = self._selected_path()
        if not path:
            return
        proj = self.store.get_by_path(path)
        if not proj:
            return
        confirm = QMessageBox.question(
            self, "Forget project",
            f"Remove '{proj.name}' from the recent list?\n\n"
            "This does NOT delete any files on disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.store.remove(proj.id)
            self._refresh()

    # ------------------------------------------------------------------
    # Phase 14g — set-up / tools actions (so you're never trapped pre-session)
    # ------------------------------------------------------------------

    def _launch(self, candidates: list[list[str]], label: str) -> None:
        """Launch the first available command, detached. Fail-safe."""
        import shutil
        import subprocess
        for cmd in candidates:
            exe = cmd[0]
            if shutil.which(exe) or Path(exe).exists():
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    continue
        QMessageBox.information(
            self, label,
            f"Couldn't start {label}. Make sure it's installed "
            "(you can install it from the Desktop session's Apps page).",
        )

    def _open_godot(self) -> None:
        import shutil
        if shutil.which("godot") or shutil.which("godot4") or shutil.which("Godot"):
            self._launch([["godot"], ["godot4"], ["Godot"]], "Godot")
            return
        # Not installed yet — offer to download + install it (the 'godot' Arch
        # package) right here, then launch it. One-click, no password (the
        # 49-outlaw polkit rule covers the installer helper).
        reply = QMessageBox.question(
            self, "Install Godot",
            "Godot isn't installed yet.\n\nDownload and install it now? "
            "It's a one-click install and takes a few minutes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from .pkg_install import install_package
        if install_package("godot", "Godot", self):
            self._launch([["godot"], ["godot4"], ["Godot"]], "Godot")

    def _open_lmstudio(self) -> None:
        # outlaw-lm-studio launches LM Studio (or opens its download page).
        self._launch(
            [["outlaw-lm-studio"], ["/usr/local/bin/outlaw-lm-studio"],
             ["lm-studio"], ["lmstudio"]],
            "LM Studio",
        )

    def _ask_v4rmint(self) -> None:
        # The base AI (V4rm1nt) is reachable right here, pre-session, for setup
        # help — no project + no LM Studio model required. Lazy import so a
        # missing openai/base-AI dep can't break the picker itself.
        try:
            from .ask_v4rmint import AskV4rmintDialog
        except Exception as exc:  # noqa: BLE001
            QMessageBox.information(self, "V4rm1nt",
                                    f"The base-AI assistant isn't available here ({exc}).")
            return
        AskV4rmintDialog(parent=self).exec()

    def _switch_to_desktop(self) -> None:
        reply = QMessageBox.question(
            self, "Switch to Desktop session",
            "Leave the Dev session and switch to the Desktop?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            home = Path.home()
            (home / ".outlaw-session").write_text("desktop\n", encoding="utf-8")
            (home / ".outlaw-session.honor-once").write_text("", encoding="utf-8")
        except OSError:
            pass
        super().reject()   # exit → the dev session restarts into the Desktop

    def _quit_app(self) -> None:
        # Bypass the first-run "no cancel" guard — an explicit Quit always exits.
        super().reject()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_godot_project(
        self, path: str, *, offer_register_anyway: bool = False,
    ) -> bool:
        """Make sure the folder still looks like a Godot project.

        If it doesn't and the project came from the recent list, offer to forget
        it. If it doesn't but the user manually picked it, offer to scaffold or
        bail. Returns True if the caller should accept() with this path.
        """
        p = Path(path)
        if not p.exists():
            QMessageBox.warning(
                self, "Folder is gone",
                f"This folder no longer exists:\n\n{path}\n\n"
                "I'll remove it from the recent list.",
            )
            existing = self.store.get_by_path(path)
            if existing:
                self.store.remove(existing.id)
                self._refresh()
            return False

        if not (p / "project.godot").exists():
            if offer_register_anyway:
                choice = QMessageBox.question(
                    self, "Not a Godot project",
                    f"There's no project.godot in:\n\n{path}\n\n"
                    "Open it anyway? (The agent will still be able to write files, "
                    "but Godot-specific tools may not work until a project is created.)",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                return choice == QMessageBox.StandardButton.Yes
            QMessageBox.warning(
                self, "Not a Godot project",
                f"project.godot is missing in:\n\n{path}\n\n"
                "It may have been moved or deleted. I'll keep it in the list for now.",
            )
            return False

        return True

    def reject(self) -> None:  # noqa: D401
        # Disallow Esc/Cancel on the first-run picker (caller passes allow_cancel=False).
        if not self._allow_cancel:
            return
        super().reject()
