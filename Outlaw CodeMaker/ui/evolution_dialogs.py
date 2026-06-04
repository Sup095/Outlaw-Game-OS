"""Phoenix Protocol dialogs: approve-before-swap review + evolution log viewer."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .styles import COLORS


class EvolutionReviewDialog(QDialog):
    """Shown when a shadow build passes validation. The user approves the live swap."""

    def __init__(self, candidate: dict, engine, parent=None):
        super().__init__(parent)
        self.candidate = candidate
        self.engine = engine
        self.approved = False
        self._shadow = Path(candidate["shadow_dir"])

        dry = candidate.get("dry_run", False)
        self.setWindowTitle("Phoenix Protocol — Review Evolution")
        self.resize(820, 600)

        verdict = (
            f'<span style="color:{COLORS["ok"]};font-weight:700;">✓ VALIDATION PASSED</span>'
            + ('  <span style="color:#838c9c;">(dry-run — swap disabled)</span>' if dry else "")
        )
        head = QLabel(
            f'<h3 style="color:{COLORS["accent2"]};margin:0;">Proposed self-upgrade</h3>'
            f'<p style="color:#c8ced8;">{candidate["rationale"]}</p>{verdict}'
        )
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)

        self.files = QListWidget()
        for rel in candidate["changed"]:
            QListWidgetItem(f"📄  {rel}", self.files)
        self.files.itemDoubleClicked.connect(self._open_diff)
        hint = QLabel("Double-click a file to see its diff.")
        hint.setStyleSheet("color:#838c9c;")

        self.vlog = QTextBrowser()
        self.vlog.setPlainText(candidate["validation_log"])
        self.vlog.setMaximumHeight(150)

        view_btn = QPushButton("View diff")
        view_btn.clicked.connect(lambda: self._open_diff(self.files.currentItem()))
        approve = QPushButton("✓  Approve & Hot-Swap" if not dry else "Approve (disabled in dry-run)")
        approve.setObjectName("PrimaryButton")
        approve.setEnabled(not dry)
        approve.clicked.connect(self._approve)
        discard = QPushButton("✕  Discard")
        discard.setObjectName("DangerButton")
        discard.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addWidget(view_btn)
        btns.addStretch(1)
        btns.addWidget(discard)
        btns.addWidget(approve)

        layout = QVBoxLayout(self)
        layout.addWidget(head)
        layout.addWidget(QLabel("Files changed:"))
        layout.addWidget(self.files, 1)
        layout.addWidget(hint)
        layout.addWidget(QLabel("Validation report:"))
        layout.addWidget(self.vlog)
        layout.addLayout(btns)

    def _open_diff(self, item) -> None:
        if not item:
            return
        rel = item.text().split("  ", 1)[-1].strip()
        old, new = self.engine.diff_for(rel, self._shadow)
        from .diff_dialog import DiffDialog
        DiffDialog(rel, old, new, "write", self).exec()

    def _approve(self) -> None:
        self.approved = True
        self.accept()


class EvolutionLogDialog(QDialog):
    """Read-only history of what the AI has changed about itself."""

    def __init__(self, rows: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Evolution Logs — what Outlaw changed about itself")
        self.resize(820, 600)

        browser = QTextBrowser()
        browser.setHtml(self._render(rows))

        close = QPushButton("Close")
        close.setObjectName("PrimaryButton")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(browser, 1)
        layout.addLayout(row)

    def _render(self, rows: list[dict]) -> str:
        if not rows:
            return f'<p style="color:{COLORS["muted"]};">No evolutions yet. Toggle REGENERATE or run a dry-run.</p>'
        out = [f'<h2 style="color:{COLORS["accent2"]};">Evolution history ({len(rows)})</h2>']
        for r in rows:
            ok = r["validation"] == "PASS"
            vcol = COLORS["ok"] if ok else COLORS["err"]
            swap = "🔥 SWAPPED" if r["swapped"] else ("dry-run" if r["dry_run"] else "not applied")
            files = ", ".join(r["files_changed"]) or "—"
            out.append(
                f'<div style="border-left:3px solid {vcol};padding:4px 10px;margin:8px 0;background:{COLORS["panel"]};">'
                f'<span style="color:{COLORS["muted"]};">{r["created_at"]}</span> · '
                f'<b style="color:{COLORS["accent2"]};">{r["mode"]}</b> · '
                f'<span style="color:{vcol};">{r["validation"]}</span> · '
                f'<span style="color:{COLORS["text"]};">{swap}</span>'
                f'<div style="color:#c8ced8;margin-top:3px;">{r["rationale"] or ""}</div>'
                f'<div style="color:{COLORS["muted"]};font-size:8pt;">files: {files}</div>'
                f'</div>'
            )
        return "".join(out)
