"""The Thought Chamber — Plan / Review / Execute panels.

Each stage gets its own scrollable text area inside a draggable vertical
splitter, so you can resize any pane (or hit ⤢ to expand one). Streaming uses
"sticky bottom" scrolling: it follows new tokens only if you're already at the
bottom — scroll up to read earlier reasoning and it won't yank you back down.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


STAGES = ("plan", "review", "execute")
STAGE_TITLES = {
    "plan":    "1 · PLAN",
    "review":  "2 · REVIEW",
    "execute": "3 · EXECUTE",
}


class _StagePane(QWidget):
    expand_requested = pyqtSignal(str)

    def __init__(self, stage: str):
        super().__init__()
        self.stage = stage

        header = QFrame()
        header.setObjectName("PanelHeader")
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(8, 4, 8, 4)
        self.title = QLabel(STAGE_TITLES[stage])
        self.title.setObjectName("PanelTitle")
        self.indicator = QLabel("idle")
        self.indicator.setStyleSheet("color: #838c9c;")
        self.expand_btn = QToolButton()
        self.expand_btn.setText("⤢")
        self.expand_btn.setToolTip("Expand / restore this pane")
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setStyleSheet(
            "QToolButton{color:#838c9c;border:none;background:transparent;padding:0 4px;}"
            "QToolButton:hover{color:#f4d27a;}"
        )
        self.expand_btn.clicked.connect(lambda: self.expand_requested.emit(self.stage))
        hbox.addWidget(self.title)
        hbox.addStretch(1)
        hbox.addWidget(self.indicator)
        hbox.addWidget(self.expand_btn)

        self.view = QTextEdit()
        self.view.setObjectName("ThoughtView")
        self.view.setReadOnly(True)
        self.view.setAcceptRichText(False)
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.setMinimumHeight(60)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.view, 1)

    def reset(self) -> None:
        self.view.clear()
        self.indicator.setText("idle")
        self.indicator.setStyleSheet("color: #838c9c;")

    def mark_active(self) -> None:
        self.indicator.setText("thinking…")
        self.indicator.setStyleSheet("color: #e0b341;")

    def mark_done(self) -> None:
        self.indicator.setText("done")
        self.indicator.setStyleSheet("color: #5cc97a;")

    def append_delta(self, text: str) -> None:
        self.mark_active()
        sb = self.view.verticalScrollBar()
        # "Sticky bottom": only auto-follow if the user is already at the bottom.
        at_bottom = sb.value() >= sb.maximum() - 6
        cursor = self.view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        # NOTE: deliberately NOT calling setTextCursor — that would force a scroll
        # and steal the view from a user who scrolled up to read.
        if at_bottom:
            sb.setValue(sb.maximum())


class ThoughtChamber(QWidget):
    """Three resizable panes (splitter) with a top progress bar."""

    def __init__(self):
        super().__init__()

        title_bar = QFrame()
        title_bar.setObjectName("PanelHeader")
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(10, 6, 10, 6)
        label = QLabel("THOUGHT CHAMBER")
        label.setObjectName("PanelTitle")
        self.progress = QProgressBar()
        self.progress.setRange(0, 3)
        self.progress.setValue(0)
        self.progress.setFormat("%v / 3 stages")
        tb_layout.addWidget(label)
        tb_layout.addStretch(1)
        tb_layout.addWidget(self.progress, 2)

        self.panes: dict[str, _StagePane] = {}
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        for s in STAGES:
            pane = _StagePane(s)
            pane.expand_requested.connect(self._on_expand)
            self.panes[s] = pane
            self.splitter.addWidget(pane)
        self.splitter.setSizes([300, 300, 300])
        self._expanded: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(title_bar)
        outer.addWidget(self.splitter, 1)

    # ----- expand / restore ----------------------------------------------

    def _on_expand(self, stage: str) -> None:
        if self._expanded == stage:
            self.splitter.setSizes([300, 300, 300])  # restore equal
            self._expanded = None
        else:
            sizes = [120, 120, 120]
            sizes[STAGES.index(stage)] = 760
            self.splitter.setSizes(sizes)
            self._expanded = stage

    # ----- slots wired by MainWindow -------------------------------------

    def reset(self) -> None:
        for pane in self.panes.values():
            pane.reset()
        self.progress.setValue(0)

    def on_stage_chunk(self, stage: str, delta: str) -> None:
        if stage in self.panes:
            self.panes[stage].append_delta(delta)

    def on_stage_done(self, stage: str, _full_text: str) -> None:
        if stage in self.panes:
            self.panes[stage].mark_done()
        done_count = sum(1 for s in STAGES if self.panes[s].indicator.text() == "done")
        self.progress.setValue(done_count)
