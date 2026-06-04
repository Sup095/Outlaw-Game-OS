"""Orchestrator session management + offline guard (no LLM server needed)."""
import pytest

from core.orchestrator import Orchestrator


@pytest.fixture
def orch(qapp, config):
    from db.schema import init_database
    init_database(config["database"]["path"])
    o = Orchestrator(config)
    yield o
    o.shutdown()


def test_starts_with_a_session(orch):
    assert orch.current_session_id is not None


def test_new_session_reuses_empty(orch):
    first = orch.current_session_id
    again = orch.new_session()
    # current session is empty, so new_session reuses it
    assert again == first


def test_new_session_after_messages_creates_new(orch):
    sid = orch.current_session_id
    orch.store.add_message(sid, "user", "something")
    new_id = orch.new_session()
    assert new_id != sid


def test_open_session_returns_messages(orch):
    sid = orch.store.create_session("Old chat", "ws")
    orch.store.add_message(sid, "user", "u1")
    orch.store.add_message(sid, "assistant", "a1")
    msgs = orch.open_session(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert orch.current_session_id == sid


def test_delete_current_session_starts_new(orch):
    sid = orch.current_session_id
    orch.store.add_message(sid, "user", "x")
    orch.delete_session(sid)
    assert orch.store.get_session(sid) is None
    assert orch.current_session_id is not None
    assert orch.current_session_id != sid


def test_offline_guard_blocks_submit(orch):
    orch.online = False
    failures = []
    orch.failed.connect(failures.append)
    orch.submit_task("do a thing")
    assert failures and "LM Studio" in failures[0]
    # No worker should have been spawned.
    assert orch._worker is None or not orch._worker.isRunning()


def test_list_session_summaries(orch):
    sid = orch.store.create_session("New session", "ws")
    orch.store.add_message(sid, "user", "hello world")
    sums = orch.list_session_summaries()
    assert any(s["preview"] == "hello world" for s in sums)


def test_session_changed_emitted_on_open(orch):
    sid = orch.store.create_session("Past", "ws")
    seen = []
    orch.session_changed.connect(lambda i, t: seen.append((i, t)))
    orch.open_session(sid)
    assert (sid, "Past") in seen


def test_new_tools_registered(orch):
    for name in [
        "vault_search", "scan_signals", "tscn_read", "tscn_add_node",
        "tscn_connect", "file_write", "file_delete",
    ]:
        assert orch.tools.has(name), f"missing tool: {name}"


def test_gate_disabled_write_applies(orch):
    orch.gate.enabled = False
    res = orch._guarded_write("Player.gd", "extends Node\n")
    assert "wrote" in res
    assert "extends Node" in orch.file_controller.read("Player.gd")


def test_gate_nochange_write_applies(orch):
    orch.file_controller.write("x.gd", "same\n")
    orch.gate.enabled = True  # identical content → gate auto-approves (no diff)
    res = orch._guarded_write("x.gd", "same\n")
    assert "wrote" in res


def test_tscn_add_node_through_orchestrator(orch):
    orch.gate.enabled = False
    orch.file_controller.write(
        "Main.tscn",
        '[gd_scene load_steps=1 format=3]\n\n[node name="Root" type="Node2D"]\n',
    )
    res = orch._tscn_add_node({"path": "Main.tscn", "name": "Coin", "type": "Area2D", "parent": "."})
    assert "Injected node 'Coin'" in res
    assert 'name="Coin"' in orch.file_controller.read("Main.tscn")


def test_vault_search_tool_empty(orch):
    out = orch._vault_search_tool("anything", 5)
    assert "vault empty" in out.lower() or out  # no crash on empty index


def test_godot_file_actually_written_to_disk(orch):
    """The whole point: approving a write lands a real file in the project folder."""
    orch.gate.enabled = False  # simulate the user approving
    res = orch._guarded_write(
        "res://scripts/Enemy.gd",
        "extends CharacterBody2D\nfunc _ready():\n    pass\n",
    )
    assert "wrote" in res
    target = orch.file_controller.root / "scripts" / "Enemy.gd"
    assert target.exists(), "file was not written to disk"
    assert "func _ready()" in target.read_text(encoding="utf-8")


def test_godot_delete_actually_removes_file(orch):
    orch.gate.enabled = False
    orch.file_controller.write("Junk.gd", "extends Node\n")
    target = orch.file_controller.root / "Junk.gd"
    assert target.exists()
    orch._guarded_delete("Junk.gd")
    assert not target.exists()


def test_resume_and_continue_past_chat(orch):
    """Reopen a previous chat AND keep talking — new messages append to it."""
    sid = orch.store.create_session("Old chat", "ws")
    orch.store.add_message(sid, "user", "hello")
    orch.store.add_message(sid, "assistant", "hi there")

    msgs = orch.open_session(sid)
    assert len(msgs) == 2
    assert orch.current_session_id == sid

    orch.memory.record_user("now add a double jump")
    after = orch.store.get_messages(sid)
    assert [m.role for m in after] == ["user", "assistant", "user"]
    assert after[-1].content == "now add a double jump"


def test_extra_godot_tools_registered(orch):
    for name in ["read_lines", "file_append", "file_move", "godot_project_info", "grep", "search_text"]:
        assert orch.tools.has(name), f"missing tool: {name}"


def test_grep_tool_finds_cross_file_references(orch):
    from core.orchestrator import ToolCall
    orch.gate.enabled = False
    orch.file_controller.write("X.gd", "signal hit\nfunc h():\n    emit_signal('hit')\n")
    orch.file_controller.write("Y.gd", "func r():\n    X.hit.connect(_on_hit)\n")
    res = orch.tools.call(ToolCall("grep", {"query": "hit"}))
    assert res.ok
    assert "X.gd" in res.payload and "Y.gd" in res.payload


def test_godot_new_project_scaffolds(orch, tmp_path):
    import copy
    from core.orchestrator import ToolCall
    proj = tmp_path / "empty_game"
    proj.mkdir()
    cfg = copy.deepcopy(orch.config)
    cfg["agent"]["workspace_root"] = str(proj)
    orch.reconfigure(cfg)

    res = orch.tools.call(ToolCall("godot_new_project", {"name": "Pixel Quest"}))
    assert res.ok
    assert (proj / "project.godot").exists()
    assert (proj / "Main.tscn").exists()
    assert (proj / "Main.gd").exists()
    assert "Pixel Quest" in (proj / "project.godot").read_text(encoding="utf-8")
    # Refuses to clobber an existing project
    res2 = orch.tools.call(ToolCall("godot_new_project", {"name": "Other"}))
    assert "already exists" in res2.payload.lower() or res2.payload.startswith("ERROR")


def test_tscn_create_makes_valid_scene(orch):
    from core.orchestrator import ToolCall
    orch.gate.enabled = False
    res = orch.tools.call(ToolCall(
        "tscn_create", {"path": "Player.tscn", "root_name": "Player", "root_type": "CharacterBody2D"}))
    assert res.ok
    content = orch.file_controller.read("Player.tscn")
    assert "[gd_scene" in content
    assert 'name="Player" type="CharacterBody2D"' in content


def test_file_append_and_move_tools(orch):
    from core.orchestrator import ToolCall
    orch.gate.enabled = False
    orch.file_controller.write("a.gd", "extends Node\n")

    res = orch.tools.call(ToolCall("file_append", {"path": "a.gd", "content": "func _ready():\n    pass\n"}))
    assert res.ok
    txt = orch.file_controller.read("a.gd")
    assert "extends Node" in txt and "func _ready()" in txt

    res2 = orch.tools.call(ToolCall("file_move", {"src": "a.gd", "dst": "scripts/a.gd"}))
    assert res2.ok
    assert "func _ready()" in orch.file_controller.read("scripts/a.gd")
    assert orch.file_controller.read("a.gd").startswith("ERROR")


def test_has_resumable_and_get_roadmap(orch):
    assert orch.has_resumable() is False
    sid = orch.current_session_id
    orch.store.save_task_state(sid, "make a platformer", "plan", ["obs1"], "paused")
    assert orch.has_resumable() is True
    orch.store.save_roadmap(orch.config["agent"]["workspace_root"],
                            {"title": "Platformer", "milestones": []})
    assert orch.get_roadmap()["title"] == "Platformer"


def test_continue_phrase_without_checkpoint_is_normal_submit(orch):
    # 'continue' with no saved checkpoint should NOT crash; offline guard handles it.
    orch.online = False
    failures = []
    orch.failed.connect(failures.append)
    orch.submit_task("continue")
    assert failures  # treated as a normal (offline-guarded) task, not a resume


def test_godot_project_info_tool(orch, tmp_path):
    import copy
    from core.orchestrator import ToolCall
    proj = tmp_path / "game"
    proj.mkdir()
    (proj / "project.godot").write_text(
        '[application]\nconfig/name="Knight Quest"\nrun/main_scene="res://Main.tscn"\n\n'
        '[autoload]\nGameState="*res://GameState.gd"\n', encoding="utf-8",
    )
    cfg = copy.deepcopy(orch.config)
    cfg["agent"]["workspace_root"] = str(proj)
    orch.reconfigure(cfg)
    res = orch.tools.call(ToolCall("godot_project_info", {}))
    assert res.ok
    assert "Knight Quest" in res.payload
    assert "GameState" in res.payload
