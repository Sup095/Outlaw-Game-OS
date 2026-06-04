"""The .tscn Architect — parse fidelity + surgical edits."""
import pytest

from vision.tscn_parser import TscnDocument, TscnError


SAMPLE = '''[gd_scene load_steps=2 format=3 uid="uid://abc"]

[ext_resource type="Script" path="res://Player.gd" id="1_x"]

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1_x")

[node name="Sprite2D" type="Sprite2D" parent="."]
position = Vector2(0, -8)
'''


def test_roundtrip_preserves_content():
    out = TscnDocument.parse(SAMPLE).serialize()
    assert 'name="Player"' in out
    assert 'position = Vector2(0, -8)' in out
    assert "[ext_resource" in out
    assert 'script = ExtResource("1_x")' in out


def test_tree_summary_lists_nodes():
    s = TscnDocument.parse(SAMPLE).tree_summary()
    assert "Player" in s and "Sprite2D" in s and "CharacterBody2D" in s


def test_add_node_appends_with_properties():
    doc = TscnDocument.parse(SAMPLE)
    doc.add_node("Coin", "Area2D", parent=".", properties={"position": "Vector2(64, 0)"})
    out = doc.serialize()
    assert 'name="Coin" type="Area2D"' in out
    assert "position = Vector2(64, 0)" in out
    assert doc.find_node("Coin") is not None


def test_add_duplicate_node_raises():
    doc = TscnDocument.parse(SAMPLE)
    with pytest.raises(TscnError):
        doc.add_node("Player", "Node2D")


def test_add_connection():
    doc = TscnDocument.parse(SAMPLE)
    doc.add_connection("body_entered", "Sprite2D", ".", "_on_body_entered")
    out = doc.serialize()
    assert 'signal="body_entered"' in out
    assert 'method="_on_body_entered"' in out


def test_set_node_property():
    doc = TscnDocument.parse(SAMPLE)
    doc.set_node_property("Sprite2D", "position", "Vector2(10, 10)")
    assert doc.find_node("Sprite2D").get_property("position") == "Vector2(10, 10)"


def test_add_ext_resource_bumps_load_steps():
    doc = TscnDocument.parse(SAMPLE)
    doc.add_ext_resource("Texture2D", "res://coin.png", "2_coin")
    out = doc.serialize()
    assert 'path="res://coin.png"' in out
    assert "load_steps=3" in out  # was 2


def test_invalid_file_raises():
    with pytest.raises(TscnError):
        TscnDocument.parse("just some text\nno scene header here")
