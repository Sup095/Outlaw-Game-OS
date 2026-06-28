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
            "Tell Outlaw where your Godot project lives and how to reach your AI engine "
            "(LM Studio or Ollama). Changes apply immediately — no restart, no editing files."
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
        # Phase 16 — one-click engine preset. CodeMaker's client is OpenAI-compatible,
        # so pointing the URL at Ollama (port 11434) runs models through Ollama as a
        # full LM Studio replacement — for CPUs that can't run LM Studio (AVX1-only).
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("LM Studio · port 1234", "lmstudio")
        self.engine_combo.addItem("Ollama · port 11434 (for AVX1-only CPUs)", "ollama")
        self.engine_combo.setCurrentIndex(1 if "11434" in (config["lm_studio"]["base_url"] or "") else 0)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
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
        self.theme_combo.addItem("Match system theme · follows the desktop (default)", "system")
        self.theme_combo.addItem("Gold Gunmetal · sci-fi fortress", "gold")
        self.theme_combo.addItem("Green Phosphor · classic terminal", "green")
        self.theme_combo.addItem("Broken · sickly, barely-holding-together", "broken")
        current_theme = (config.get("ui") or {}).get("theme") or "system"
        for idx in range(self.theme_combo.count()):
            if self.theme_combo.itemData(idx) == current_theme:
                self.theme_combo.setCurrentIndex(idx)
                break

        # QoL — per-field tooltips (the labels are necessarily terse).
        self.url_edit.setToolTip("Where your AI engine listens — LM Studio: http://localhost:1234/v1 · "
                                 "Ollama: http://127.0.0.1:11434/v1")
        self.model_edit.setToolTip("Leave as 'local-model' to auto-detect LM Studio's loaded model, "
                                   "or enter an Ollama tag like qwen2.5-coder:7b.")
        self.temp_spin.setToolTip("Higher = more creative/varied; lower = more focused/repeatable. "
                                  "0.7 is a good default for coding.")
        self.title_edit.setToolTip("The window-title text Outlaw matches to find your Godot editor "
                                   "(for screenshots / on-screen error reading).")
        self.engine_combo.setToolTip("LM Studio or Ollama — picking one fills the Backend URL for you.")
        self.vram_combo.setToolTip("Shrinks the per-turn context when memory is tight so it keeps "
                                   "working on modest hardware. Auto is recommended.")

        form.addRow("Godot project folder", self.project_row)
        form.addRow("AI engine", self.engine_combo)
        form.addRow("Backend URL", self.url_edit)
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

    def _on_engine_changed(self, _idx: int) -> None:
        # Fill the backend URL + a sensible model default for the chosen engine;
        # the user can still edit both.
        if self.engine_combo.currentData() == "ollama":
            self.url_edit.setText("http://127.0.0.1:11434/v1")
            self.model_edit.setPlaceholderText("an Ollama tag, e.g. qwen2.5-coder:7b")
            # #12 — Ollama needs a REAL model tag; LM Studio's 'local-model' sentinel
            # 404s on Ollama's OpenAI endpoint. If the field still holds the sentinel
            # (or is blank), pre-fill the recommended coder tag so it works out of the
            # box (pull it from Settings → AI on the desktop, or `ollama pull`).
            if self.model_edit.text().strip() in ("", "local-model"):
                self.model_edit.setText("qwen2.5-coder:7b")
        else:
            self.url_edit.setText("http://localhost:1234/v1")
            self.model_edit.setPlaceholderText("local-model = auto-detect the loaded model")
            # Restore LM Studio's auto-detect sentinel if leaving the Ollama default.
            if self.model_edit.text().strip() in ("", "qwen2.5-coder:7b"):
                self.model_edit.setText("local-model")

    def _save(self) -> None:
        cfg = copy.deepcopy(self._config)
        cfg["agent"]["workspace_root"] = self.project_row.text() or cfg["agent"]["workspace_root"]
        cfg["lm_studio"]["base_url"] = self.url_edit.text() or cfg["lm_studio"]["base_url"]
        cfg["lm_studio"]["model"] = self.model_edit.text() or "local-model"
        cfg["lm_studio"]["temperature"] = round(self.temp_spin.value(), 2)
        cfg.setdefault("vision", {})["godot_window_title_match"] = self.title_edit.text() or "Godot"
        cfg.setdefault("godot", {})["log_path"] = self.log_row.text()
        cfg.setdefault("vram_saver", {})["mode"] = self.vram_combo.currentData() or "auto"
        cfg.setdefault("ui", {})["theme"] = self.theme_combo.currentData() or "system"
        self.result_config = cfg
        self.accept()
