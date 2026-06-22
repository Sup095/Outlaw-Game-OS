"""In-app Quick Start guide + recommended LM Studio system prompt."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .styles import COLORS


# Optional — the app sends its own per-stage system prompts via the API, so this
# is only used if you chat with the model directly in LM Studio's playground.
RECOMMENDED_SYSTEM_PROMPT = (
    "You are the local engine for Outlaw CodeMaker, an autonomous Godot 4.x "
    "coding agent. Follow the instructions in each request exactly, including "
    "any <<TOOL>>{json}<<END>> and <<ANSWER>>...<<END>> output markers. Write "
    "complete, correct GDScript/Python. Be precise and concise; do not add "
    "commentary outside the requested format. When unsure, inspect files with "
    "the provided tools before editing."
)


def _guide_html(model: str) -> str:
    a, a2, ok = COLORS["accent"], COLORS["accent2"], COLORS["ok"]
    return f"""
    <h2 style="color:{a2};">⟁ Outlaw CodeMaker — Quick Start</h2>
    <p style="color:#9aa6b2;">Detected model: <b style="color:{ok};">{model or 'connect LM Studio'}</b></p>

    <h3 style="color:{a};">New here? Read this first</h3>
    <p>Outlaw is an AI helper that <b>writes and edits the files of your game for you</b>.
    You type what you want in plain English ("make the player jump higher"), and it edits
    the actual code in your Godot project. You don't need to know how to code.</p>
    <p style="color:{ok};"><b>Nothing changes until you say so.</b> Every edit is shown to you
    first as a side-by-side comparison (a "diff"): the old version on the left, the new on the
    right. You press <b>Enter</b> to approve (it saves to your project) or <b>Esc</b> to reject.</p>

    <h3 style="color:{a};">How your changes reach the game</h3>
    <ol>
      <li>You ask for something → the AI plans and writes the new file.</li>
      <li>A <b>diff window</b> pops up. Approve with <b>Enter</b>.</li>
      <li>The file is <b>saved into your Godot project folder</b> on disk (it tells you the exact path).</li>
      <li>Click back into the <b>Godot editor</b> — it auto-reloads the changed file. Press Play to test.</li>
    </ol>

    <h3 style="color:{a};">1 · Launch (on Outlaw OS)</h3>
    <ul>
      <li>At the boot <b>greeter</b>, choose the <b>Dev session</b> — that's Outlaw CodeMaker.</li>
      <li>Before you open a project you get a <b>launcher</b>: open <b>Godot</b> or <b>LM Studio</b>
          (install either if it's missing), ask <b>V4rm1nt</b> for setup help, switch to the Desktop,
          or quit.</li>
      <li>The header dot turns <span style="color:{ok};">green</span> when your AI engine is reachable.</li>
    </ul>

    <h3 style="color:{a};">2 · Your AI engine</h3>
    <ul>
      <li><b>V4rm1nt (built-in):</b> a small bundled model that works out of the box — great on
          low-end PCs and for setup help. Ask it from the launcher or the <b>Tools</b> menu anytime.</li>
      <li><b>LM Studio:</b> load a bigger model → <b>Start Server</b> on port <b>1234</b> (the model ID
          is auto-detected) for stronger coding. Choose your engine in <b>Settings</b>.</li>
      <li><b>Ollama:</b> an alternative backend for PCs that can't run LM Studio (e.g. AVX1-only CPUs).</li>
      <li><b>Resources:</b> the Vault (RAG) is CPU-only (zero VRAM), and the VRAM/RAM saver shrinks
          context automatically when memory is tight — so it keeps working on modest hardware.</li>
    </ul>

    <h3 style="color:{a};">3 · Pick or start a Godot project</h3>
    <ul>
      <li>The <b>Project Picker</b> (when CodeMaker opens) creates a new game or reopens a recent
          one — no config files to edit by hand.</li>
      <li>Press <b>Ctrl+R</b> to index the project into the Vault so the AI can see your code.</li>
      <li>Open the Godot editor — the app auto-detects its window for screenshots/OCR.</li>
      <li><i>Optional self-heal log:</i> point <code>godot.log_path</code> in config.json at your
          project's <code>godot.log</code> for precise error capture (OCR works without it).</li>
    </ul>

    <h3 style="color:{a};">4 · Working with it</h3>
    <ul>
      <li>Type a task ("add double-jump to Player.gd"). Watch <b>SCOUT → PLAN → WRITE → VERIFY</b>.</li>
      <li>Every file write pops a <b>diff</b> — approve with <b>Enter</b>, deny with <b>Esc</b>.</li>
      <li><b>History</b> tab reopens past sessions (even from weeks ago) with full context.</li>
      <li><b>Tools ▸ Self-Healing</b> auto-fixes Godot errors it sees on screen / in the log.</li>
    </ul>

    <h3 style="color:{a};">Keyboard shortcuts</h3>
    <table cellpadding="3">
      <tr><td style="color:{a2};"><b>Enter</b></td><td>Send task</td>
          <td style="color:{a2};"><b>Esc</b></td><td>Stop agent</td></tr>
      <tr><td style="color:{a2};"><b>Ctrl+N</b></td><td>New session</td>
          <td style="color:{a2};"><b>Ctrl+L</b></td><td>Focus input</td></tr>
      <tr><td style="color:{a2};"><b>Ctrl+R</b></td><td>Reindex Vault</td>
          <td style="color:{a2};"><b>F1</b></td><td>This guide</td></tr>
      <tr><td style="color:{a2};"><b>Enter / Y</b></td><td>Approve diff</td>
          <td style="color:{a2};"><b>Esc / N</b></td><td>Deny diff</td></tr>
    </table>

    <p style="color:#9aa6b2;">The "Copy system prompt" button below copies an optional LM Studio
    playground prompt — not required, since Outlaw sends its own per-stage prompts.</p>
    """


class GuideDialog(QDialog):
    def __init__(self, model: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Outlaw CodeMaker — Quick Start")
        self.resize(720, 680)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setHtml(_guide_html(model))

        copy_btn = QPushButton("Copy system prompt")
        copy_btn.clicked.connect(self._copy_prompt)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("PrimaryButton")
        close_btn.clicked.connect(self.accept)

        row = QHBoxLayout()
        row.addWidget(copy_btn)
        row.addStretch(1)
        row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.browser, 1)
        layout.addLayout(row)

    def _copy_prompt(self) -> None:
        QApplication.clipboard().setText(RECOMMENDED_SYSTEM_PROMPT)
        self.browser.append(
            f'<p style="color:{COLORS["ok"]};">✓ System prompt copied to clipboard.</p>'
        )
