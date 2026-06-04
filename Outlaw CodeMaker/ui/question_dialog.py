"""Question dialogs — the agent asks YOU.

- _QuestionField renders one question (pick an option / type your own / free text).
- QuestionDialog wraps a single question (kept for compatibility).
- MultiQuestionDialog stacks several questions in one scrollable prompt, so the
  agent can ask everything up front and continue automatically once you Submit.

Spec per question:
    {"question": "...", "options": ["A","B","C"], "allow_custom": true}
    {"question": "...", "free_text": true}
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .styles import COLORS


class _QuestionField(QWidget):
    """One question with its answer controls. Call value() for the answer."""

    def __init__(self, spec: dict, number: int | None = None):
        super().__init__()
        question = str(spec.get("question", "")) or "(no question)"
        options = [str(o) for o in (spec.get("options") or [])]
        self._free_text = bool(spec.get("free_text")) or not options
        allow_custom = spec.get("allow_custom", True)

        prefix = f"{number}. " if number else ""
        q = QLabel(f"{prefix}{question}")
        q.setWordWrap(True)
        q.setStyleSheet(f"color:{COLORS['accent2']}; font-size:11pt; font-weight:600;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 10)
        layout.setSpacing(5)
        layout.addWidget(q)

        self._group = QButtonGroup(self)
        self._radios: list[tuple[QRadioButton, str]] = []
        self._other_radio: QRadioButton | None = None
        self.custom_edit = QLineEdit()
        self.free_edit = QPlainTextEdit()

        if self._free_text:
            self.free_edit.setPlaceholderText("Type your answer…")
            self.free_edit.setFixedHeight(80)
            layout.addWidget(self.free_edit)
        else:
            for i, opt in enumerate(options):
                rb = QRadioButton(opt)
                rb.setStyleSheet("QRadioButton{padding:3px;}")
                if i == 0:
                    rb.setChecked(True)
                self._group.addButton(rb)
                self._radios.append((rb, opt))
                layout.addWidget(rb)
            if allow_custom:
                self._other_radio = QRadioButton("✏  Other — type my own:")
                self._group.addButton(self._other_radio)
                layout.addWidget(self._other_radio)
                self.custom_edit.setPlaceholderText("Your own answer…")
                self.custom_edit.textEdited.connect(lambda _t: self._other_radio.setChecked(True))
                layout.addWidget(self.custom_edit)

        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{COLORS['border']};")
        layout.addWidget(line)

    def value(self) -> str:
        if self._free_text:
            return self.free_edit.toPlainText().strip() or "(skipped)"
        if self._other_radio is not None and self._other_radio.isChecked():
            return self.custom_edit.text().strip() or "(skipped)"
        for rb, opt in self._radios:
            if rb.isChecked():
                return opt
        return "(skipped)"


class QuestionDialog(QDialog):
    """Single-question dialog (compatibility)."""

    def __init__(self, spec: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Outlaw CodeMaker is asking…")
        self.setMinimumWidth(560)
        self.answer = ""
        self._field = _QuestionField(spec)

        submit = QPushButton("Submit  (Enter)")
        submit.setObjectName("PrimaryButton")
        submit.setDefault(True)
        submit.clicked.connect(self._submit)
        skip = QPushButton("Skip")
        skip.clicked.connect(self._skip)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(skip)
        btns.addWidget(submit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addWidget(self._field)
        layout.addLayout(btns)

    def _submit(self) -> None:
        self.answer = self._field.value()
        self.accept()

    def _skip(self) -> None:
        self.answer = "(skipped — use sensible defaults)"
        self.accept()


class MultiQuestionDialog(QDialog):
    """All of the agent's questions in one prompt; Submit returns every answer."""

    def __init__(self, specs: list[dict], parent=None):
        super().__init__(parent)
        n = len(specs)
        self.setWindowTitle(f"Outlaw CodeMaker — {n} question{'s' if n != 1 else ''}")
        self.resize(640, min(760, 180 + n * 150))
        self.answers: list[str] = []

        intro = QLabel(
            "Answer these so I can build exactly what you want. Pick an option or type "
            "your own — then Submit and I'll continue automatically."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{COLORS['muted']};")

        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(4, 4, 4, 4)
        self._fields: list[_QuestionField] = []
        for i, spec in enumerate(specs, start=1):
            field = _QuestionField(spec, number=i)
            self._fields.append(field)
            col.addWidget(field)
        col.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)

        submit = QPushButton("Submit all  ➤")
        submit.setObjectName("PrimaryButton")
        submit.setDefault(True)
        submit.clicked.connect(self._submit)
        skip = QPushButton("Skip all")
        skip.clicked.connect(self._skip)

        btns = QHBoxLayout()
        btns.addWidget(QLabel("Submitting continues the build automatically."))
        btns.addStretch(1)
        btns.addWidget(skip)
        btns.addWidget(submit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(intro)
        layout.addWidget(scroll, 1)
        layout.addLayout(btns)

    def _submit(self) -> None:
        self.answers = [f.value() for f in self._fields]
        self.accept()

    def _skip(self) -> None:
        self.answers = ["(skipped — use sensible defaults)"] * len(self._fields)
        self.accept()
