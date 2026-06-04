"""Diff alignment + stats (pure functions behind the side-by-side viewer)."""
from ui.diff_dialog import build_diff_rows, diff_stats


def test_identical_is_all_equal():
    rows = build_diff_rows("a\nb\n", "a\nb\n")
    assert all(r[2] == "eq" and r[5] == "eq" for r in rows)
    assert diff_stats("a\nb", "a\nb") == (0, 0)


def test_pure_addition():
    assert diff_stats("a\n", "a\nb\n") == (1, 0)


def test_pure_deletion():
    assert diff_stats("a\nb\n", "a\n") == (0, 1)


def test_replacement_counts_both():
    added, removed = diff_stats("a\nold\nc\n", "a\nnew\nc\n")
    assert added == 1 and removed == 1


def test_new_file_from_empty():
    added, removed = diff_stats("", "line1\nline2\n")
    assert added == 2 and removed == 0


def test_rows_stay_aligned():
    rows = build_diff_rows("x\ny\nz\n", "x\nY\nz\n")
    # every row carries a left and a right slot (4-column alignment holds)
    assert all(len(r) == 6 for r in rows)
