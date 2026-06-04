"""Signal-Bus Mapper — signal decls, emits, and autoload singletons."""
from vision.signal_mapper import SignalMapper


def _project(tmp_path):
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="Demo"\n\n'
        '[autoload]\nGameState="*res://GameState.gd"\nAudio="res://Audio.gd"\n'
    )
    (tmp_path / "Player.gd").write_text(
        "extends CharacterBody2D\n"
        "signal died\n"
        "signal scored(points)\n"
        "func _ready():\n"
        "    emit_signal('died')\n"
        "    health.changed.connect(_on_health)\n"
    )
    return tmp_path


def test_finds_autoloads(tmp_path):
    smap = SignalMapper(str(_project(tmp_path))).scan()
    assert smap.autoloads.get("GameState", "").endswith("GameState.gd")
    assert "Audio" in smap.autoloads


def test_finds_signal_declarations(tmp_path):
    smap = SignalMapper(str(_project(tmp_path))).scan()
    all_signals = [s for sc in smap.scripts for s in sc.signals]
    assert "died" in all_signals
    assert "scored" in all_signals
    assert smap.total_signals >= 2


def test_summary_mentions_key_items(tmp_path):
    smap = SignalMapper(str(_project(tmp_path))).scan()
    summary = smap.summary()
    assert "GameState" in summary
    assert "died" in summary


def test_empty_dir_is_safe(tmp_path):
    smap = SignalMapper(str(tmp_path)).scan()
    assert smap.total_signals == 0
    assert smap.autoloads == {}
    assert "No signals" in smap.summary()
