"""Atomic writes — no .tmp left behind, overwrites cleanly."""
from core.atomic import atomic_write


def test_creates_file_and_dirs(tmp_path):
    p = tmp_path / "sub" / "deep" / "f.py"
    atomic_write(p, "x = 1\n")
    assert p.read_text() == "x = 1\n"
    assert not (p.parent / "f.py.tmp").exists()


def test_overwrite_is_clean(tmp_path):
    p = tmp_path / "f.txt"
    atomic_write(p, "alpha")
    atomic_write(p, "beta")
    assert p.read_text() == "beta"
    assert list(p.parent.glob("*.tmp")) == []
