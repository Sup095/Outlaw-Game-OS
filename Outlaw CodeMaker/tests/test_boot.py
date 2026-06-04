"""Full UI boot (offscreen) + progress/session/markdown wiring, server offline."""
import pytest

from core.orchestrator import Orchestrator
from ui.main_window import MainWindow


@pytest.fixture
def window(qapp, config):
    from db.schema import init_database
    init_database(config["database"]["path"])
    orch = Orchestrator(config)
    win = MainWindow(orch, config)
    yield win
    win.close()
    orch.shutdown()


def test_window_builds(window):
    assert window.windowTitle() == "Outlaw CodeMaker"
    # left tabs present: Workspace | History | Roadmap | Snapshots | Steam
    assert window.left_tabs.count() == 5
    assert window.left_tabs.tabText(0) == "Workspace"
    assert window.left_tabs.tabText(1) == "History"
    assert window.left_tabs.tabText(2).startswith("Roadmap")
    assert window.left_tabs.tabText(3) == "Snapshots"
    assert window.left_tabs.tabText(4) == "Steam"


def test_progress_slot_updates_bar(window):
    window._on_progress(42, "Reviewing…")
    assert window.progress_bar.value() == 42
    assert "Reviewing" in window.progress_bar.format()


def test_progress_clamped(window):
    window._on_progress(150, "x")
    assert window.progress_bar.value() == 100
    window._on_progress(-10, "y")
    assert window.progress_bar.value() == 0


def test_session_changed_updates_labels(window):
    window.orch.session_changed.emit(7, "My Godot Task")
    assert "My Godot Task" in window.session_label.text()
    assert "My Godot Task" in window.chat_panel.subtitle.text()


def test_connection_changed_updates_header(window):
    window._on_connection_changed(True, "model: qwen2.5-coder")
    assert "LM Studio" in window.conn_label.text()
    window._on_connection_changed(False, "offline")
    assert "offline" in window.conn_label.text()


def test_render_history_into_chat(window):
    msgs = [
        {"role": "user", "content": "make a player", "metadata": None},
        {"role": "assistant", "content": "```gd\nextends Node\n```", "metadata": None},
        {"role": "tool", "content": "wrote 10 bytes", "metadata": {"tool": "file_write"}},
    ]
    window.chat_panel.render_history(msgs)
    html = window.chat_panel.view.toHtml()
    assert "make a player" in html
    assert "extends Node" in html


def test_offline_submit_shows_message(window):
    window.orch.online = False
    window.input.setText("do something")
    window._on_send()
    html = window.chat_panel.view.toHtml()
    assert "LM Studio" in html  # failure message rendered as system msg


def test_app_icon_is_drawn(qapp):
    from ui.styles import make_app_icon
    icon = make_app_icon()
    assert not icon.isNull()
