"""FileController: CRUD, virtual workspace tree, and sandbox enforcement."""
import pytest

from tools.file_controller import FileController


@pytest.fixture
def fc(tmp_path):
    return FileController(str(tmp_path / "ws"))


def test_write_read_roundtrip(fc):
    assert "wrote" in fc.write("Player.gd", "extends CharacterBody2D\n")
    assert "CharacterBody2D" in fc.read("Player.gd")


def test_nested_write_creates_dirs(fc):
    fc.write("scripts/ai/State.gd", "extends Node\n")
    assert "extends Node" in fc.read("scripts/ai/State.gd")


def test_list_tree_and_search(fc):
    fc.write("a.gd", "1")
    fc.write("sub/b.gd", "2")
    tree = fc.list_tree(depth=4)
    assert "a.gd" in tree and "sub" in tree
    matches = fc.search("*.gd")
    assert "a.gd" in matches and "sub/b.gd" in matches


def test_delete_file_and_dir(fc):
    fc.write("x/y.gd", "z")
    assert "removed" in fc.delete("x")
    assert "not found" in fc.read("x/y.gd")


def test_sandbox_blocks_traversal(fc):
    with pytest.raises(PermissionError):
        fc.read("../../secrets.txt")
    with pytest.raises(PermissionError):
        fc.write("../escape.gd", "nope")


def test_refuses_unknown_binary_type(fc):
    fc.write("blob.bin", "data")  # write allowed
    assert "refusing" in fc.read("blob.bin").lower()


def test_file_count(fc):
    fc.write("a.gd", "1")
    fc.write("b.gd", "2")
    assert fc.file_count() == 2


def test_godot_res_path_normalized(fc):
    # The model naturally uses Godot's res:// style — it must map to the project root.
    fc.write("res://scripts/Player.gd", "extends CharacterBody2D\n")
    assert "CharacterBody2D" in fc.read("scripts/Player.gd")
    assert "CharacterBody2D" in fc.read("res://scripts/Player.gd")


def test_leading_slash_and_backslash_normalized(fc):
    fc.write("/a/b.gd", "x")
    assert "x" in fc.read("a/b.gd")
    fc.write("c\\d.gd", "y")          # backslashes from Windows-style paths
    assert "y" in fc.read("c/d.gd")


def test_user_path_rejected(fc):
    with pytest.raises(PermissionError):
        fc.write("user://savegame.dat", "z")


def test_read_lines_slice(fc):
    fc.write("big.gd", "\n".join(f"line{i}" for i in range(1, 6)) + "\n")
    out = fc.read_lines("big.gd", 2, 4)
    assert "2: line2" in out and "4: line4" in out
    assert "1: line1" not in out and "5: line5" not in out


def test_search_text_finds_symbol_across_files(fc):
    fc.write("Player.gd", "signal player_died\nfunc die():\n    emit_signal('player_died')\n")
    fc.write("UI.gd", "func _ready():\n    Player.player_died.connect(_on_died)\n")
    fc.write("unrelated.gd", "func foo():\n    pass\n")
    out = fc.search_text("player_died")
    assert "Player.gd" in out and "UI.gd" in out
    assert "unrelated.gd" not in out


def test_search_text_no_match(fc):
    fc.write("a.gd", "extends Node\n")
    assert "no matches" in fc.search_text("does_not_exist_anywhere")


def test_search_text_regex(fc):
    fc.write("a.gd", "func jump():\n    pass\nfunc dash():\n    pass\n")
    out = fc.search_text(r"func \w+\(\)", regex=True)
    assert "jump" in out and "dash" in out
