"""parse_control — ASK / ROADMAP / CONTINUE / TOOL / ANSWER extraction."""
from core.orchestrator import parse_control


def test_parse_ask_with_options():
    c = parse_control('<<ASK>>{"question": "Genre?", "options": ["RPG", "Platformer"]}<<END>>')
    assert len(c.asks) == 1
    assert c.asks[0]["question"] == "Genre?"
    assert c.asks[0]["options"] == ["RPG", "Platformer"]
    assert c.answer is None and not c.tools


def test_multiple_ask_blocks_collected_for_one_batch():
    text = (
        '<<ASK>>{"question": "Genre?", "options": ["RPG", "Platformer"]}<<END>>\n'
        '<<ASK>>{"question": "2D or 3D?", "options": ["2D", "3D"]}<<END>>\n'
        '<<ASK>>{"question": "Name?", "free_text": true}<<END>>'
    )
    c = parse_control(text)
    assert len(c.asks) == 3
    assert [a["question"] for a in c.asks] == ["Genre?", "2D or 3D?", "Name?"]


def test_parse_roadmap():
    c = parse_control(
        '<<ROADMAP>>{"title": "My Game", "milestones": '
        '[{"name": "Movement", "status": "active", "items": ["walk"]}]}<<END>>'
    )
    assert c.roadmap and c.roadmap["title"] == "My Game"
    assert c.roadmap["milestones"][0]["name"] == "Movement"


def test_parse_answer_plus_continue():
    c = parse_control("<<ANSWER>>\nFinished part 1.\n<<END>>\n<<CONTINUE>>")
    assert c.answer == "Finished part 1."
    assert c.wants_continue is True


def test_parse_tool_and_ask_together():
    c = parse_control(
        '<<TOOL>>{"name": "file_read", "args": {"path": "Player.gd"}}<<END>>\n'
        '<<ASK>>{"question": "Pixel art or 3D?", "free_text": true}<<END>>'
    )
    assert len(c.tools) == 1 and c.tools[0].name == "file_read"
    assert len(c.asks) == 1 and c.asks[0].get("free_text") is True


def test_parse_strips_think_blocks():
    c = parse_control("<think>internal reasoning</think><<ANSWER>>\nhi\n<<END>>")
    assert c.answer == "hi"


def test_malformed_ask_is_skipped():
    c = parse_control("<<ASK>>{not valid json}<<END>>")
    assert c.asks == []


def test_no_markers_is_empty():
    c = parse_control("just prose, no markers")
    assert not c.tools and not c.asks and c.roadmap is None
    assert c.answer is None and c.wants_continue is False


def test_write_marker_becomes_file_write_with_real_newlines():
    c = parse_control(
        '<<WRITE file="scripts/Player.gd">>\n'
        "extends CharacterBody2D\nfunc _ready():\n    pass\n<<END>>"
    )
    assert len(c.tools) == 1
    assert c.tools[0].name == "file_write"
    assert c.tools[0].args["path"] == "scripts/Player.gd"
    content = c.tools[0].args["content"]
    assert "func _ready()" in content and "\n" in content  # real newlines preserved


def test_write_marker_handles_quotes_and_braces():
    # Content that would BREAK one-line JSON is perfectly fine in a <<WRITE>> block.
    c = parse_control('<<WRITE file="x.gd">>\nvar d = {"hp": 10}\nfunc say(): print("hi")\n<<END>>')
    content = c.tools[0].args["content"]
    assert 'var d = {"hp": 10}' in content
    assert 'print("hi")' in content


def test_append_marker_becomes_file_append():
    c = parse_control('<<APPEND file="a.gd">>\nfunc extra():\n    pass\n<<END>>')
    assert c.tools and c.tools[0].name == "file_append"
    assert c.tools[0].args["path"] == "a.gd"


def test_multiple_write_blocks_in_one_turn():
    # A feature spanning several files: write all of them in a single turn.
    text = (
        '<<WRITE file="scripts/Coin.gd">>\nextends Area2D\n<<END>>\n'
        '<<WRITE file="scripts/Player.gd">>\nextends CharacterBody2D\n<<END>>\n'
        '<<TOOL>>{"name": "file_read", "args": {"path": "Main.tscn"}}<<END>>'
    )
    c = parse_control(text)
    write_paths = [t.args["path"] for t in c.tools if t.name == "file_write"]
    assert write_paths == ["scripts/Coin.gd", "scripts/Player.gd"]
    assert any(t.name == "file_read" for t in c.tools)


def test_malformed_tool_json_is_surfaced_not_dropped():
    # A literal newline inside the JSON string is invalid -> must be reported so the
    # loop can coach the model to use <<WRITE>> instead of silently stopping.
    c = parse_control('<<TOOL>>{"name": "file_write", "args": {"content": "line1\nline2"}}<<END>>')
    assert c.tool_errors  # non-empty
    assert not c.tools
