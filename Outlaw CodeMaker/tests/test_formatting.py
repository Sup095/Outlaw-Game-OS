"""Markdown→HTML rendering for the chat panel, incl. injection safety."""
from ui.formatting import to_html


def test_bold_and_inline_code():
    out = to_html("Use **velocity** with `move_and_slide()`")
    assert "<b>velocity</b>" in out
    assert "<code" in out and "move_and_slide()" in out


def test_fenced_code_block_preserved():
    out = to_html("Here:\n```gdscript\nextends Node\nfunc _ready():\n    pass\n```")
    assert "<pre" in out
    assert "extends Node" in out
    assert "func _ready():" in out


def test_html_is_escaped_no_injection():
    out = to_html("<script>alert('x')</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_code_block_escapes_html_inside():
    out = to_html("```\n<b>not bold</b>\n```")
    assert "&lt;b&gt;not bold&lt;/b&gt;" in out
    assert "<b>not bold</b>" not in out


def test_bullets_render_list():
    out = to_html("- one\n- two")
    assert "<ul" in out and out.count("<li>") == 2


def test_heading_renders():
    out = to_html("# Title")
    assert "Title" in out and "font-weight:700" in out


def test_empty_input():
    assert to_html("") == ""
