"""Tool-call and answer extraction from Execute-stage text (pure functions)."""
from core.orchestrator import parse_answer, parse_tool_calls, strip_think


def test_strip_think_removes_block():
    assert "secret" not in strip_think("<think>secret reasoning</think>hello")
    assert strip_think("<think>x</think>hi").strip() == "hi"


def test_strip_think_unclosed_block():
    # Truncated stream — strip to end.
    assert strip_think("<think>partial reasoning, no close tag").strip() == ""


def test_strip_think_preserves_normal_text():
    assert strip_think("no think tags here") == "no think tags here"


def test_strip_think_then_parse_answer():
    text = "<think>I should write the file</think>\n<<ANSWER>>\nDone.\n<<END>>"
    assert parse_answer(strip_think(text)) == "Done."


def test_non_godot_python_answer_parses_without_tools():
    # Simulates a Qwen3 EXECUTE output: a <think> block, then a plain-Python answer.
    # Proves regular code comes through and NO Godot tools are invoked.
    raw = (
        "<think>The user wants pure Python, no Godot involved. I'll just answer.</think>\n"
        "<<ANSWER>>\n"
        "Here's the helper:\n"
        "```python\n"
        "def reverse_words(s):\n"
        "    return ' '.join(s.split()[::-1])\n"
        "```\n"
        "<<END>>"
    )
    clean = strip_think(raw)
    calls, _ = parse_tool_calls(clean)
    ans = parse_answer(clean)
    assert calls == []                      # no tools → no Godot dependency
    assert ans is not None
    assert "def reverse_words" in ans
    assert "```python" in ans


def test_parse_single_tool_call():
    text = '<<TOOL>>{"name": "file_read", "args": {"path": "Player.gd"}}<<END>>'
    calls, errors = parse_tool_calls(text)
    assert errors == []
    assert len(calls) == 1
    assert calls[0].name == "file_read"
    assert calls[0].args["path"] == "Player.gd"


def test_parse_multiple_tool_calls():
    text = (
        '<<TOOL>>{"name": "file_mkdir", "args": {"path": "scripts"}}<<END>>\n'
        '<<TOOL>>{"name": "file_write", "args": {"path": "scripts/x.gd", "content": "a"}}<<END>>'
    )
    calls, _ = parse_tool_calls(text)
    assert [c.name for c in calls] == ["file_mkdir", "file_write"]


def test_parse_answer_block():
    text = "<<ANSWER>>\nAll done — created the file.\n<<END>>"
    assert parse_answer(text) == "All done — created the file."


def test_tool_and_answer_together():
    text = (
        '<<TOOL>>{"name": "list_workspace", "args": {}}<<END>>\n'
        "<<ANSWER>>\nHere is your tree.\n<<END>>"
    )
    calls, _ = parse_tool_calls(text)
    assert len(calls) == 1
    assert parse_answer(text) == "Here is your tree."


def test_malformed_json_is_skipped():
    text = '<<TOOL>>{not valid json}<<END>>'
    calls, errors = parse_tool_calls(text)
    assert calls == []
    assert len(errors) == 1  # the bad block is reported, not silently dropped


def test_no_markers_returns_empty_and_none():
    calls, errors = parse_tool_calls("just prose")
    assert calls == [] and errors == []
    assert parse_answer("just prose") is None
