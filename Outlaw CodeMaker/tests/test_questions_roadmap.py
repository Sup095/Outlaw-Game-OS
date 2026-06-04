"""QuestionGate handshake, question/roadmap dialogs, roadmap + task_state DAO."""
import pytest
from PyQt6.QtCore import Qt

from core.approval import QuestionGate
from db.schema import MemoryStore


# ----- QuestionGate (blocking handshake) ---------------------------------

def test_question_gate_resolves(qapp):
    g = QuestionGate()
    g.question_requested.connect(lambda spec, t: g.resolve(t, "RPG"),
                                 Qt.ConnectionType.DirectConnection)
    assert g.ask({"question": "Genre?", "options": ["RPG", "Platformer"]}) == "RPG"


def test_question_gate_cancel_returns_default(qapp):
    g = QuestionGate()
    # no handler resolves; cancel_all releases with the default
    g.question_requested.connect(lambda spec, t: g.cancel_all(),
                                 Qt.ConnectionType.DirectConnection)
    ans = g.ask({"question": "anything?"})
    assert isinstance(ans, str)


def test_question_gate_ask_batch(qapp):
    g = QuestionGate()
    g.batch_question_requested.connect(
        lambda specs, t: g.resolve_batch(t, ["RPG", "Pixel art"]),
        Qt.ConnectionType.DirectConnection,
    )
    answers = g.ask_batch([
        {"question": "Genre?", "options": ["RPG", "Platformer"]},
        {"question": "Art style?", "free_text": True},
    ])
    assert answers == ["RPG", "Pixel art"]


def test_multi_question_dialog(qapp):
    from ui.question_dialog import MultiQuestionDialog
    d = MultiQuestionDialog([
        {"question": "Genre?", "options": ["RPG", "Platformer"]},
        {"question": "Name?", "free_text": True},
    ])
    d._fields[1].free_edit.setPlainText("Hero Quest")
    d._submit()
    assert len(d.answers) == 2
    assert d.answers[0] in ("RPG", "Platformer")
    assert "Hero" in d.answers[1]


# ----- dialogs build ------------------------------------------------------

def test_question_dialog_options(qapp):
    from ui.question_dialog import QuestionDialog
    d = QuestionDialog({"question": "Genre?", "options": ["RPG", "Platformer"]})
    d._submit()  # first option selected by default
    assert d.answer in ("RPG", "Platformer")


def test_question_dialog_free_text(qapp):
    from ui.question_dialog import QuestionDialog
    d = QuestionDialog({"question": "Describe your game", "free_text": True})
    d._field.free_edit.setPlainText("a cozy farming sim")
    d._submit()
    assert "farming" in d.answer


def test_roadmap_view_renders(qapp):
    from ui.roadmap_view import RoadmapView
    rv = RoadmapView()
    rv.show_data({"title": "My Game", "milestones": [
        {"name": "Movement", "status": "active",
         "items": [{"text": "walk", "done": True}, {"text": "jump", "done": False}]},
    ]})
    html = rv.view.toHtml()
    assert "My Game" in html and "walk" in html and "jump" in html
    rv.show_data(None)
    assert "No roadmap" in rv.view.toHtml()


# ----- persistence --------------------------------------------------------

def test_roadmap_save_get(store: MemoryStore):
    store.save_roadmap("F:/g", {"title": "T", "milestones": []})
    assert store.get_roadmap("F:/g")["title"] == "T"
    assert store.get_roadmap("F:/missing") is None
    # upsert overwrites
    store.save_roadmap("F:/g", {"title": "T2", "milestones": []})
    assert store.get_roadmap("F:/g")["title"] == "T2"


def test_task_state_roundtrip(store: MemoryStore):
    sid = store.create_session("S", "ws")
    store.save_task_state(sid, "make a game", "the plan", ["[tool] did x"], "paused")
    st = store.get_task_state(sid)
    assert st["user_task"] == "make a game"
    assert st["reviewed_plan"] == "the plan"
    assert st["observations"] == ["[tool] did x"]
    assert st["status"] == "paused"
    store.clear_task_state(sid)
    assert store.get_task_state(sid) is None
