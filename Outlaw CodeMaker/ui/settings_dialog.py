"""Settings — point Outlaw at a Godot project and configure LM Studio, in-app.

No config.json editing required. Saving writes config.json atomically and the
orchestrator applies it live (rebuilds the client + workspace components).
"""

from __future__ import annotations

import copy

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .styles import COLORS


class _PathRow(QWidget):
    """A line edit + Browse button for a folder or file path."""

    def __init__(self, value: str, pick_dir: bool, caption: str):
        super().__init__()
        self.pick_dir = pick_dir
        self.caption = caption
        self.edit = QLineEdit(value)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(browse)

    def _browse(self) -> None:
        start = self.edit.text() or ""
        if self.pick_dir:
            path = QFileDialog.getExistingDirectory(self, self.caption, start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, self.caption, start)
        if path:
            self.edit.setText(path.replace("\\", "/"))

    def text(self) -> str:
        return self.edit.text().strip()


class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Outlaw CodeMaker — Settings")
        self.resize(640, 440)
        self._config = config
        self.result_config: dict | None = None

        intro = QLabel(
            "Tell Outlaw where your Godot project lives and how to reach LM Studio. "
            "Changes apply immediately — no restart, no editing files."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{COLORS['muted']};")

        form = QFormLayout()
        form.setSpacing(10)

        self.project_row = _PathRow(config["agent"]["workspace_root"], True,
                                    "Choose your Godot project folder")
        self.url_edit = QLineEdit(config["lm_studio"]["base_url"])
        self.model_edit = QLineEdit(config["lm_studio"].get("model", "local-model"))
        self.model_edit.setPlaceholderText("local-model = auto-detect the loaded model")
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(float(config["lm_studio"].get("temperature", 0.7)))
        self.title_edit = QLineEdit(config["vision"].get("godot_window_title_match", "Godot"))
        self.log_row = _PathRow(config.get("godot", {}).get("log_path", ""), False,
                                "Select your project's godot.log (optional)")

        # Aggressive VRAM saver — auto reads NVML each turn; the explicit modes
        # force-pin. Labels match the shell-side System Core (SC7) so the user
        # sees the same vocabulary across the Desktop and Dev sessions.
        # Always-on free savings (lazy imports, CPU-only RAG, model id caching,
        # poller-pause-on-hide) are inherent in how the codebase is built and
        # stay on regardless of which mode is selected here.
        self.vram_combo = QComboBox()
        self.vram_combo.addItem("Auto · kicks in below 1.5 GB free (recommended)", "auto")
        self.vram_combo.addItem("Off · never trim, even when VRAM is low", "off")
        self.vram_combo.addItem("Always Lean · halve context every turn", "lean")
        self.vram_combo.addItem("Always Minimal · bare-minimum context every turn", "minimal")
        current_pref = (config.get("vram_saver") or {}).get("mode", "auto")
        for idx in range(self.vram_combo.count()):
            if self.vram_combo.itemData(idx) == current_pref:
                self.vram_combo.setCurrentIndex(idx)
                break

        # P1 — visual theme. Default gold-on-gunmetal (the house style); green is
        # the classic phosphor look that matches the desktop shell. Applies on
        # next launch (no live re-style, to keep things simple and crash-free).
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Gold Gunmetal · sci-fi fortress (default)", "gold")
        self.theme_combo.addItem("Green Phosphor · classic terminal", "green")
        current_theme = "green" if (config.get("ui") or {}).get("theme") == "green" else "gold"
        for idx in range(self.theme_combo.count()):
            if self.theme_combo.itemData(idx) == current_theme:
                self.theme_combo.setCurrentIndex(idx)
                break

        form.addRow("Godot project folder", self.project_row)
        form.addRow("LM Studio URL", self.url_edit)
        form.addRow("Model", self.model_edit)
        form.addRow("Temperature", self.temp_spin)
        form.addRow("Aggressive VRAM saver", self.vram_combo)
        form.addRow("Theme (restart to apply)", self.theme_combo)
        form.addRow("Godot window title", self.title_edit)
        form.addRow("Godot log (self-heal)", self.log_row)

        hint = QLabel(
            "Tip: the Godot project folder is the one containing <b>project.godot</b>. "
            "Leave the log blank — on-screen error reading works without it."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{COLORS['muted']};font-size:9pt;")

        save = QPushButton("Save & Apply")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self._save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(intro)
        layout.addLayout(form, 1)
        layout.addWidget(hint)
        layout.addLayout(btns)

    def _save(self) -> None:
        cfg = copy.deepcopy(self._config)
        cfg["agent"]["workspace_root"] = self.project_row.text() or cfg["agent"]["workspace_root"]
        cfg["lm_studio"]["base_url"] = self.url_edit.text() or cfg["lm_studio"]["base_url"]
        cfg["lm_studio"]["model"] = self.model_edit.text() or "local-model"
        cfg["lm_studio"]["temperature"] = round(self.temp_spin.value(), 2)
        cfg.setdefault("vision", {})["godot_window_title_match"] = self.title_edit.text() or "Godot"
        cfg.setdefault("godot", {})["log_path"] = self.log_row.text()
        cfg.setdefault("vram_saver", {})["mode"] = self.vram_combo.currentData() or "auto"
        cfg.setdefault("ui", {})["theme"] = self.theme_combo.currentData() or "gold"
        self.result_config = cfg
        self.accept()
