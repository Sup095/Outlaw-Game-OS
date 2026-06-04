"""MemoryStore DAO + MemoryManager: sessions, messages, traces, resume, auto-title."""
from db.schema import MemoryStore
from core.memory import MemoryManager


def test_session_message_roundtrip(store: MemoryStore):
    sid = store.create_session("Test", "ws")
    store.add_message(sid, "user", "hello")
    store.add_message(sid, "assistant", "hi")
    msgs = store.get_messages(sid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "hello"


def test_rename_and_get_session(store: MemoryStore):
    sid = store.create_session("Old", "ws")
    store.rename_session(sid, "New Title")
    assert store.get_session(sid).title == "New Title"


def test_delete_cascades_messages(store: MemoryStore):
    sid = store.create_session("S", "ws")
    store.add_message(sid, "user", "x")
    store.delete_session(sid)
    assert store.get_session(sid) is None
    assert store.get_messages(sid) == []


def test_session_summaries(store: MemoryStore):
    sid = store.create_session("New session", "ws")
    store.add_message(sid, "user", "first thing")
    store.add_message(sid, "assistant", "ok")
    sums = store.list_session_summaries()
    assert len(sums) == 1
    s = sums[0]
    assert s["message_count"] == 2
    assert s["preview"] == "first thing"


def test_reasoning_traces(store: MemoryStore):
    sid = store.create_session("S", "ws")
    mid = store.add_message(sid, "user", "task")
    store.save_reasoning_trace(mid, "plan", "the plan")
    store.save_reasoning_trace(mid, "review", "the review")
    traces = store.get_reasoning_traces(mid)
    assert [t["stage"] for t in traces] == ["plan", "review"]


def test_is_session_empty(store: MemoryStore):
    sid = store.create_session("S", "ws")
    assert store.is_session_empty(sid)
    store.add_message(sid, "user", "x")
    assert not store.is_session_empty(sid)


def test_manager_auto_titles_from_first_message(store: MemoryStore):
    mgr = MemoryManager(store)
    ctx = mgr.start_session("New session", "ws")
    mgr.record_user("Add a finite state machine to Player.gd please")
    sess = store.get_session(ctx.session_id)
    assert sess.title.startswith("Add a finite state machine")
    assert sess.title != "New session"


def test_manager_resume_loads_history(store: MemoryStore):
    sid = store.create_session("S", "ws")
    store.add_message(sid, "user", "u1")
    store.add_message(sid, "assistant", "a1")
    mgr = MemoryManager(store)
    ctx = mgr.resume_session(sid, "S", "ws")
    assert len(ctx.chat) == 2
    assert ctx.chat[0].role == "user" and ctx.chat[0].content == "u1"


def test_long_title_truncated(store: MemoryStore):
    mgr = MemoryManager(store)
    mgr.start_session("New session", "ws")
    long_text = "word " * 50
    mgr.record_user(long_text)
    title = mgr.current.title
    assert len(title) <= 50 and title.endswith("…")
