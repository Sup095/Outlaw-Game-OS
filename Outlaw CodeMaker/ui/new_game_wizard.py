"""New Game wizard — walks you through bootstrapping a fresh Godot project.

Two modes, picked on page 1:

    Quick     — five fixed questions (Name, Dimension, Genre, Style, Mechanics).
                Predictable, fast, fully offline. Best when you already know what
                you want.

    Guided    — wizard collects Name + Dimension, then closes; the caller fires
                off an orchestrator task that hands the rest to LM Studio. The
                agent then asks follow-ups dynamically via the existing
                ``MultiQuestionDialog`` gate. Best when you want help shaping it.

Both modes end the same way: a fresh Godot project on disk, registered in the
projects DB, with a ``.outlaw/project.json`` sidecar describing what you asked
for. The caller decides whether to start a "design this game" agent task.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from vision.godot_project import scaffold_files, write_outlaw_metadata

from .question_dialog import _QuestionField
from .styles import COLORS


logger = logging.getLogger(__name__)


# Where the bundled scaffolds live. Each entry's `path` is resolved against
# Outlaw CodeMaker's repo root (parent of `ui/`). Templates added later just
# drop a folder under assets/templates/<id>/ and append an entry here.
_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "assets" / "templates"


@dataclass(frozen=True)
class TemplateInfo:
    id: str            # folder name under assets/templates/
    label: str
    description: str
    dimension_hint: str = ""  # nudge the user if their pick doesn't match


# Order here = render order on the template page. "empty" is the first and
# default option so the wizard's behavior is unchanged for users who don't
# care about templates.
TEMPLATES: list[TemplateInfo] = [
    TemplateInfo(
        id="empty",
        label="Empty (just the scaffold)",
        description="A blank, ready-to-run Godot project — nothing extra. Start completely fresh and tell Outlaw what to build.",
    ),
    TemplateInfo(
        id="platformer_2d",
        label="Platformer (2D)",
        description="A side-scrolling platformer starter: your character already runs and jumps with smooth, forgiving controls. You add the levels, art and enemies.",
        dimension_hint="2D",
    ),
    TemplateInfo(
        id="topdown_shooter_2d",
        label="Top-down shooter (2D)",
        description="A top-down shooter starter: move with the keyboard, aim with the mouse, and shooting is stubbed in for Outlaw to finish. You add enemies and art.",
        dimension_hint="2D",
    ),
    TemplateInfo(
        id="walker_3d",
        label="First-person walker (3D)",
        description="A first-person 3D starter: walk around and look with the mouse. Drop in your world and Outlaw fills in the rest.",
        dimension_hint="3D",
    ),
    TemplateInfo(
        id="visual_novel",
        label="Visual novel",
        description="A visual-novel starter: shows dialogue line by line, click to advance. You add the characters, script and backgrounds.",
    ),
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class WizardResult:
    """What the wizard returns to the caller after a successful run."""
    mode: str                 # "quick" or "guided"
    name: str
    project_path: Path        # absolute path to the new project folder
    dimension: str            # "2D" / "3D" / "2.5D"
    template_id: str = "empty"  # id from TEMPLATES (see assets/templates/)
    genre: str = ""           # filled by quick mode only
    style: str = ""           # filled by quick mode only
    mechanics: str = ""       # filled by quick mode only
    start_guided_session: bool = False  # true for guided mode → caller starts an AI task
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Reusable widgets
# ---------------------------------------------------------------------------


class _ChoiceTile(QFrame):
    """A big clickable tile used for the mode and dimension pages."""

    def __init__(self, title: str, subtitle: str, button_group: QButtonGroup):
        super().__init__()
        self.setObjectName("ChoiceTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(self._qss(selected=False))

        self.radio = QRadioButton()
        self.radio.setVisible(False)  # logical only; the tile IS the affordance
        button_group.addButton(self.radio)
        self.radio.toggled.connect(self._on_toggled)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"color: {COLORS['accent2']}; font-weight: 800; font-size: 14pt;"
        )
        sub_lbl = QLabel(subtitle)
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color: {COLORS['muted']};")

        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 20)   # C15 — breathing room on wizard pages
        col.setSpacing(10)
        col.addWidget(title_lbl)
        col.addWidget(sub_lbl)
        col.addWidget(self.radio)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.radio.setChecked(True)
        super().mousePressEvent(event)

    def _on_toggled(self, checked: bool) -> None:
        self.setStyleSheet(self._qss(selected=checked))

    def _qss(self, selected: bool) -> str:
        border = COLORS["accent"] if selected else COLORS["border"]
        bg = COLORS["panel_hi"] if selected else COLORS["panel"]
        return (
            f"QFrame#ChoiceTile {{"
            f"  background-color: {bg};"
            f"  border: 2px solid {border};"
            f"  border-radius: 10px;"
            f"}}"
        )


class _PathPicker(QWidget):
    """Line edit + Browse, for the parent folder field."""

    def __init__(self, value: str):
        super().__init__()
        self.edit = QLineEdit(value)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(browse)

    def _browse(self) -> None:
        start = self.edit.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Choose parent folder", start)
        if path:
            self.edit.setText(path.replace("\\", "/"))

    def value(self) -> str:
        return self.edit.text().strip()


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------


# Indices into self.stack — keeping them named so navigation logic is readable.
P_MODE = 0
P_BASICS = 1
P_DIMENSION = 2
P_GENRE = 3
P_STYLE = 4
P_MECHANICS = 5
P_GUIDED_INTRO = 6
P_SUMMARY = 7
P_TEMPLATE = 8  # appended last; both flows insert it between DIMENSION and the rest

QUICK_FLOW = [P_MODE, P_BASICS, P_DIMENSION, P_TEMPLATE, P_GENRE, P_STYLE, P_MECHANICS, P_SUMMARY]
GUIDED_FLOW = [P_MODE, P_BASICS, P_DIMENSION, P_TEMPLATE, P_GUIDED_INTRO, P_SUMMARY]


class NewGameWizard(QDialog):
    """Multi-page wizard that ends with a fresh Godot project on disk.

    Read ``result`` after exec() — None means the user cancelled.
    """

    def __init__(self, default_parent_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Outlaw CodeMaker — New Game")
        self.resize(760, 640)
        self.setMinimumSize(560, 460)

        self.default_parent_dir = default_parent_dir
        self.mode: str = "quick"
        self.result: WizardResult | None = None

        self._build_ui()
        self._set_flow_position(0)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Header
        self.title_label = QLabel("New Game")
        self.title_label.setStyleSheet(
            f"color: {COLORS['accent2']}; font-size: 16pt; font-weight: 800;"
        )
        self.step_label = QLabel("")
        self.step_label.setStyleSheet(f"color: {COLORS['muted']};")
        header_row = QHBoxLayout()
        header_row.addWidget(self.title_label)
        header_row.addStretch(1)
        header_row.addWidget(self.step_label)

        # Stack of pages
        self.stack = QStackedWidget()
        self.stack.addWidget(self._make_mode_page())       # P_MODE
        self.stack.addWidget(self._make_basics_page())     # P_BASICS
        self.stack.addWidget(self._make_dimension_page())  # P_DIMENSION
        self.stack.addWidget(self._make_genre_page())      # P_GENRE
        self.stack.addWidget(self._make_style_page())      # P_STYLE
        self.stack.addWidget(self._make_mechanics_page())  # P_MECHANICS
        self.stack.addWidget(self._make_guided_page())     # P_GUIDED_INTRO
        self.stack.addWidget(self._make_summary_page())    # P_SUMMARY
        self.stack.addWidget(self._make_template_page())   # P_TEMPLATE

        # Footer
        self.back_btn = QPushButton("← Back")
        self.back_btn.clicked.connect(self._on_back)
        self.next_btn = QPushButton("Next →")
        self.next_btn.setObjectName("PrimaryButton")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._on_next)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        footer_row = QHBoxLayout()
        footer_row.addWidget(self.cancel_btn)
        footer_row.addStretch(1)
        footer_row.addWidget(self.back_btn)
        footer_row.addWidget(self.next_btn)

        # Wrap the page stack in a scroll area so tall pages (the template list,
        # long summaries / descriptions) can scroll instead of being clipped.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.stack)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addLayout(header_row)
        layout.addWidget(scroll, 1)
        layout.addLayout(footer_row)

    # ----- Pages -----

    def _make_mode_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        explain = QLabel(
            "How would you like to design your game?"
        )
        explain.setStyleSheet(f"color: {COLORS['text']}; font-size: 12pt; font-weight: 600;")

        self.mode_group = QButtonGroup(self)
        self.mode_quick = _ChoiceTile(
            "Quick start",
            "Five questions, you fill them in. Best when you already know what "
            "you want to build. Works fully offline.",
            self.mode_group,
        )
        self.mode_guided = _ChoiceTile(
            "Guided design session",
            "I'll ask follow-ups based on what you say — pause whenever I'm "
            "unsure, multiple-choice or free-text. Best when you want help "
            "shaping the idea. Needs LM Studio connected.",
            self.mode_group,
        )
        self.mode_quick.radio.setChecked(True)

        layout.addWidget(explain)
        layout.addSpacing(6)
        layout.addWidget(self.mode_quick)
        layout.addWidget(self.mode_guided)
        layout.addStretch(1)
        return page

    def _make_basics_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        explain = QLabel("Name your game and pick where to create the project folder.")
        explain.setStyleSheet(f"color: {COLORS['muted']};")

        name_label = QLabel("Game name")
        name_label.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 700;")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. The Knight's Quest")

        parent_label = QLabel("Parent folder")
        parent_label.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 700;")
        self.parent_picker = _PathPicker(str(self.default_parent_dir).replace("\\", "/"))

        hint = QLabel(
            "I'll create a subfolder under the parent. So 'Cool Game' inside "
            "'F:/Godot Games' becomes 'F:/Godot Games/Cool_Game/'."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['muted']}; font-size: 9pt;")

        layout.addWidget(explain)
        layout.addWidget(name_label)
        layout.addWidget(self.name_edit)
        layout.addSpacing(6)
        layout.addWidget(parent_label)
        layout.addWidget(self.parent_picker)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _make_dimension_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        explain = QLabel("What dimension is your game?")
        explain.setStyleSheet(f"color: {COLORS['text']}; font-size: 12pt; font-weight: 600;")

        self.dim_group = QButtonGroup(self)
        self.dim_2d = _ChoiceTile(
            "2D",
            "Sprites, tilemaps, side-view or top-down. Lowest VRAM cost.",
            self.dim_group,
        )
        self.dim_3d = _ChoiceTile(
            "3D",
            "Full 3D with meshes, lighting, and cameras. Highest VRAM cost.",
            self.dim_group,
        )
        self.dim_25d = _ChoiceTile(
            "2.5D",
            "3D world rendered with a 2D viewpoint, or sprites in a 3D scene. "
            "Isometric, billboards, parallax.",
            self.dim_group,
        )
        self.dim_2d.radio.setChecked(True)

        layout.addWidget(explain)
        layout.addWidget(self.dim_2d)
        layout.addWidget(self.dim_3d)
        layout.addWidget(self.dim_25d)
        layout.addStretch(1)
        return page

    def _make_genre_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.genre_field = _QuestionField({
            "question": "What genre is it?",
            "options": [
                "Platformer",
                "RPG",
                "Shooter",
                "Puzzle",
                "Adventure",
                "Roguelike",
                "Simulation",
                "Strategy",
            ],
            "allow_custom": True,
        })
        layout.addWidget(self.genre_field)
        layout.addStretch(1)
        return page

    def _make_style_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.style_field = _QuestionField({
            "question": "Style and setting — describe the world, vibe, art direction.",
            "free_text": True,
        })
        layout.addWidget(self.style_field)
        layout.addStretch(1)
        return page

    def _make_mechanics_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.mechanics_field = _QuestionField({
            "question": "Core mechanics — what do players actually DO?",
            "free_text": True,
        })
        layout.addWidget(self.mechanics_field)
        layout.addStretch(1)
        return page

    def _make_template_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        explain = QLabel("Pick a starter template — or stay with Empty for a blank project.")
        explain.setStyleSheet(f"color: {COLORS['text']}; font-size: 12pt; font-weight: 600;")
        sub = QLabel("Templates drop in a working Player script + a couple of helpers so the "
                     "agent has something to iterate on instead of starting from a blank file.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(explain)
        layout.addWidget(sub)

        self.template_group = QButtonGroup(self)
        # Keep parallel arrays so the navigation logic can ask "which is selected".
        self._template_tiles: list[tuple[_ChoiceTile, TemplateInfo]] = []
        for info in TEMPLATES:
            extra = f"  ·  best for {info.dimension_hint}" if info.dimension_hint else ""
            tile = _ChoiceTile(info.label, info.description + extra, self.template_group)
            layout.addWidget(tile)
            self._template_tiles.append((tile, info))
        if self._template_tiles:
            self._template_tiles[0][0].radio.setChecked(True)  # Empty by default

        layout.addStretch(1)
        return page

    def _selected_template(self) -> TemplateInfo:
        for tile, info in getattr(self, "_template_tiles", []):
            if tile.radio.isChecked():
                return info
        # Fallback to Empty — never crash on an unexpected state.
        return TEMPLATES[0]

    def _make_guided_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        title = QLabel("Ready to design together")
        title.setStyleSheet(f"color: {COLORS['accent2']}; font-size: 13pt; font-weight: 700;")

        body = QLabel(
            "When you click Next on the summary page, I'll scaffold the project, "
            "then start a design session. I'll ask follow-ups based on what you "
            "told me — multiple choice when I can list options, free text when I "
            "need plot or world details. Pop-ups will pause the build until you "
            "answer. The side log and progress bar update live."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {COLORS['text']};")

        notes_label = QLabel("Anything you want me to know up front? (optional)")
        notes_label.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 700;")
        self.guided_notes = QPlainTextEdit()
        self.guided_notes.setPlaceholderText(
            "e.g. 'I want it to feel like Hollow Knight crossed with Stardew Valley' "
            "or 'the player is a librarian in 1920s Vienna'…"
        )
        self.guided_notes.setFixedHeight(120)

        layout.addWidget(title)
        layout.addWidget(body)
        layout.addSpacing(6)
        layout.addWidget(notes_label)
        layout.addWidget(self.guided_notes)
        layout.addStretch(1)
        return page

    def _make_summary_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        title = QLabel("Ready to create")
        title.setStyleSheet(f"color: {COLORS['accent2']}; font-size: 13pt; font-weight: 700;")

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        self.summary_label.setStyleSheet(
            f"color: {COLORS['text']}; "
            f"background: {COLORS['panel']}; "
            f"border: 1px solid {COLORS['border']}; "
            f"border-radius: 8px; padding: 14px;"
        )

        hint = QLabel(
            "Clicking Create scaffolds a valid Godot 4 project at the path above, "
            "registers it in your projects list, and writes a .outlaw/project.json "
            "sidecar with the answers."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['muted']}; font-size: 9pt;")

        layout.addWidget(title)
        layout.addWidget(self.summary_label)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _flow(self) -> list[int]:
        return QUICK_FLOW if self.mode == "quick" else GUIDED_FLOW

    def _set_flow_position(self, pos: int) -> None:
        """Show the page at position ``pos`` within the current mode's flow."""
        flow = self._flow()
        pos = max(0, min(pos, len(flow) - 1))
        page_index = flow[pos]
        self.stack.setCurrentIndex(page_index)
        self.step_label.setText(f"Step {pos + 1} of {len(flow)}")

        self.back_btn.setEnabled(pos > 0)
        is_last = pos == len(flow) - 1
        self.next_btn.setText("Create Game" if is_last else "Next →")

        if page_index == P_SUMMARY:
            self._render_summary()

    def _current_pos(self) -> int:
        idx = self.stack.currentIndex()
        flow = self._flow()
        return flow.index(idx) if idx in flow else 0

    def _on_next(self) -> None:
        idx = self.stack.currentIndex()

        # Validate / harvest current page before advancing
        if idx == P_MODE:
            self.mode = "quick" if self.mode_quick.radio.isChecked() else "guided"
        elif idx == P_BASICS:
            if not self._validate_basics():
                return
        elif idx == P_SUMMARY:
            self._finish()
            return  # _finish accepts / rejects

        # Advance one step in the current flow
        self._set_flow_position(self._current_pos() + 1)

    def _on_back(self) -> None:
        self._set_flow_position(self._current_pos() - 1)

    # ------------------------------------------------------------------
    # Validation + summary rendering
    # ------------------------------------------------------------------

    def _validate_basics(self) -> bool:
        name = self.name_edit.text().strip()
        parent = self.parent_picker.value()
        if not name:
            QMessageBox.warning(self, "Need a name", "Please give the game a name first.")
            return False
        if not parent:
            QMessageBox.warning(self, "Need a folder", "Pick a parent folder for the project.")
            return False
        if not Path(parent).exists():
            QMessageBox.warning(
                self, "Folder doesn't exist",
                f"The parent folder doesn't exist yet:\n\n{parent}\n\nCreate it first or pick a different one.",
            )
            return False
        target = Path(parent) / _safe_slug(name)
        if target.exists() and any(target.iterdir()):
            QMessageBox.warning(
                self, "Folder is not empty",
                f"This folder already exists and has files in it:\n\n{target}\n\n"
                "Pick a different name, or move/delete the existing folder.",
            )
            return False
        return True

    def _render_summary(self) -> None:
        name = self.name_edit.text().strip()
        parent = self.parent_picker.value()
        target = Path(parent) / _safe_slug(name)
        dim = self._selected_dimension()

        template = self._selected_template()
        rows = [
            ("Mode", "Quick start" if self.mode == "quick" else "Guided design session"),
            ("Name", name or "(unset)"),
            ("Location", str(target).replace("\\", "/")),
            ("Dimension", dim),
            ("Template", template.label),
        ]
        if self.mode == "quick":
            rows += [
                ("Genre", self.genre_field.value()),
                ("Style", self._truncate(self.style_field.value())),
                ("Mechanics", self._truncate(self.mechanics_field.value())),
            ]
        else:
            notes = self.guided_notes.toPlainText().strip()
            rows.append(("Notes for the agent", self._truncate(notes) or "(none)"))

        html = "<table cellpadding='4' style='font-size:10pt'>"
        for k, v in rows:
            html += (
                f"<tr><td style='color:{COLORS['muted']};vertical-align:top;'>{k}</td>"
                f"<td style='color:{COLORS['text']};'>{v}</td></tr>"
            )
        html += "</table>"
        self.summary_label.setText(html)

    @staticmethod
    def _truncate(s: str, limit: int = 180) -> str:
        s = (s or "").strip()
        if len(s) <= limit:
            return s
        return s[:limit] + "…"

    def _selected_dimension(self) -> str:
        if self.dim_3d.radio.isChecked():
            return "3D"
        if self.dim_25d.radio.isChecked():
            return "2.5D"
        return "2D"

    # ------------------------------------------------------------------
    # Finish — scaffold project, write sidecar, set result
    # ------------------------------------------------------------------

    def _finish(self) -> None:
        name = self.name_edit.text().strip()
        parent = Path(self.parent_picker.value())
        target = parent / _safe_slug(name)
        dim = self._selected_dimension()

        try:
            target.mkdir(parents=True, exist_ok=True)
            for rel, content in scaffold_files(name):
                file_path = target / rel
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — any failure here must NOT crash the app
            logger.exception("New Game scaffold failed")
            QMessageBox.critical(
                self, "Couldn't create project",
                f"Failed to write the project files:\n\n{exc}",
            )
            return

        # Copy the selected template's files on top of the scaffold. Template
        # files OVERWRITE colliding scaffold files — e.g. a template can ship
        # its own Main.gd. The empty template is a no-op (folder is empty).
        template = self._selected_template()
        if template.id != "empty":
            try:
                copied = _apply_template(template.id, target)
            except OSError as exc:
                # Don't bail — the scaffold is good, the template was a nice-to-have.
                logger.warning("Template '%s' partially copied: %s", template.id, exc)
                copied = []
                QMessageBox.warning(
                    self, "Template warning",
                    f"The project was created but the '{template.label}' template files "
                    f"couldn't be copied fully:\n\n{exc}\n\nYou can keep going — the agent "
                    "will fill in anything that's missing.",
                )
            else:
                logger.info("Applied template %s: %d file(s).", template.id, len(copied))

        metadata: dict = {
            "name": name,
            "dimension": dim,
            "template": template.id,
            "created_with": "outlaw-codemaker-wizard",
            "mode": self.mode,
        }
        if self.mode == "quick":
            metadata["genre"] = self.genre_field.value()
            metadata["style"] = self.style_field.value()
            metadata["mechanics"] = self.mechanics_field.value()
        else:
            metadata["initial_notes"] = self.guided_notes.toPlainText().strip()
        # Reserve the Steam config block in the shape the Steam panel reads.
        # The user fills these in later via Steam panel → Save Steam config.
        # See core/steam_publish.SteamConfig for the canonical schema.
        metadata.setdefault("steam", {
            "app_id": "",
            "build_account": "",
            "default_branch": "default",
            "depots": [],
        })
        metadata.setdefault("build_dir", "build")

        try:
            write_outlaw_metadata(target, metadata)
        except OSError as exc:
            QMessageBox.warning(
                self, "Sidecar warning",
                f"Project was created, but I couldn't write .outlaw/project.json:\n\n{exc}\n\n"
                "You can still proceed — Steam publishing will need you to add it later.",
            )

        self.result = WizardResult(
            mode=self.mode,
            name=name,
            project_path=target,
            dimension=dim,
            template_id=template.id,
            genre=metadata.get("genre", ""),
            style=metadata.get("style", ""),
            mechanics=metadata.get("mechanics", ""),
            start_guided_session=(self.mode == "guided"),
            metadata=metadata,
        )
        self.accept()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_slug(name: str) -> str:
    """Folder-safe slug from a human game name. Keeps it readable."""
    cleaned = "".join(c if c.isalnum() or c in (" ", "_", "-") else "" for c in name)
    cleaned = cleaned.strip().replace(" ", "_")
    return cleaned or "New_Game"


def _apply_template(template_id: str, project_root: Path) -> list[Path]:
    """Copy ``assets/templates/<template_id>/`` onto ``project_root`` in place.

    Files in the template overwrite scaffold files at the same path. Returns
    the list of destination paths actually written.
    """
    src = _TEMPLATES_ROOT / template_id
    if not src.exists() or not src.is_dir():
        return []
    written: list[Path] = []
    for srcfile in src.rglob("*"):
        if not srcfile.is_file():
            continue
        rel = srcfile.relative_to(src)
        # Skip dot-marker helper files (e.g. .keep) — they're just to make
        # empty dirs survive git, not part of the actual scaffold.
        if rel.name.startswith(".keep"):
            continue
        dst = project_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(srcfile, dst)
        written.append(dst)
    return written


def build_guided_kickoff_task(result: WizardResult) -> str:
    """Compose the agent task that 'Guided' mode kicks off after the wizard.

    Caller passes this to ``Orchestrator.submit_task``. The agent should
    immediately use the existing ``<<ASK>>`` mechanism to gather more design
    details before writing any code.
    """
    notes = result.metadata.get("initial_notes") or ""
    extra = f"\n\nUser's initial notes:\n{notes}" if notes else ""
    return (
        "DESIGN SESSION — a fresh Godot project was just scaffolded and the user "
        "wants you to design the rest of the game with them. Don't write code "
        "yet. First use <<ASK>> blocks to gather the design details you need: "
        "genre, style, core mechanics, target playtime, art direction, "
        "win/lose conditions, key scenes. Ask several questions at once when "
        "you can (the user prefers batched questions). Once you have enough, "
        "emit a <<ROADMAP>> block, then start building.\n\n"
        f"Project name: {result.name}\n"
        f"Dimension: {result.dimension}\n"
        f"Project folder: {result.project_path}{extra}"
    )
