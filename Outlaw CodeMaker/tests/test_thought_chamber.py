"""Thought Chamber — content retention (scrollable), expand toggle, progress."""
from ui.thought_chamber import STAGES, ThoughtChamber


def test_all_chunks_retained_for_scrolling(qapp):
    tc = ThoughtChamber()
    for _ in range(60):
        tc.on_stage_chunk("plan", "a line of reasoning\n")
    text = tc.panes["plan"].view.toPlainText()
    # Nothing is dropped — every line is there to scroll back through.
    assert text.count("a line of reasoning") == 60


def test_expand_toggle_state(qapp):
    tc = ThoughtChamber()
    tc._on_expand("execute")
    assert tc._expanded == "execute"
    tc._on_expand("execute")          # toggle back to equal
    assert tc._expanded is None


def test_reset_clears_all_panes(qapp):
    tc = ThoughtChamber()
    tc.on_stage_chunk("review", "thinking")
    tc.on_stage_done("review", "thinking")
    tc.reset()
    assert tc.panes["review"].view.toPlainText() == ""
    assert tc.progress.value() == 0


def test_stage_done_advances_progress(qapp):
    tc = ThoughtChamber()
    tc.on_stage_done("plan", "x")
    assert tc.progress.value() == 1
    tc.on_stage_done("review", "y")
    assert tc.progress.value() == 2


def test_three_panes_present(qapp):
    tc = ThoughtChamber()
    assert set(tc.panes.keys()) == set(STAGES)
    assert tc.splitter.count() == 3
