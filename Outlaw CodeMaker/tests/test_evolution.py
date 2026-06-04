"""Evolution engine — discovery, path safety, shadow/apply/swap, proposal parsing.

The heavy validate() path (full headless app init) is proven by the dry-run, not
here, so the unit suite stays fast. These tests use a tiny fake project.
"""
import pytest

from core.evolution import EvolutionEngine, EvolutionError


def _fake_project(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "__init__.py").write_text("")
    (tmp_path / "core" / "version.py").write_text('VERSION = "1"\nEVOLUTION_COUNT = 0\nBUILD = "x"\n')
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "__init__.py").write_text("")
    (tmp_path / "main.py").write_text("print('hi')\n")
    (tmp_path / "config.json").write_text("{}")
    return tmp_path


def test_discover_lists_modules(tmp_path):
    eng = EvolutionEngine(_fake_project(tmp_path))
    d = eng.discover()
    paths = [m["path"] for m in d["manifest"]]
    assert "core/version.py" in paths
    assert d["bundle"]  # small files get bundled for the LLM


def test_path_safety():
    eng = EvolutionEngine(".")
    assert eng._is_allowed_target("core/x.py")
    assert eng._is_allowed_target("main.py")
    assert not eng._is_allowed_target("../evil.py")
    assert not eng._is_allowed_target("secrets.txt")
    assert not eng._is_allowed_target("workspace/x.py")
    assert not eng._is_allowed_target("core/x.txt")


def test_create_shadow_then_apply_leaves_live_untouched(tmp_path):
    eng = EvolutionEngine(_fake_project(tmp_path))
    shadow = eng.create_shadow()
    assert (shadow / "core" / "version.py").exists()
    applied = eng.apply_changes(shadow, [{"path": "core/version.py", "content": "VERSION = '2'\n"}])
    assert applied == ["core/version.py"]
    assert "VERSION = '2'" in (shadow / "core" / "version.py").read_text()
    # live file is NOT modified by apply
    assert 'VERSION = "1"' in (tmp_path / "core" / "version.py").read_text()


def test_apply_rejects_disallowed_path(tmp_path):
    eng = EvolutionEngine(_fake_project(tmp_path))
    shadow = eng.create_shadow()
    with pytest.raises(EvolutionError):
        eng.apply_changes(shadow, [{"path": "../evil.py", "content": "x"}])


def test_swap_writes_live(tmp_path):
    eng = EvolutionEngine(_fake_project(tmp_path))
    shadow = eng.create_shadow()
    eng.apply_changes(shadow, [{"path": "core/version.py", "content": "VERSION = '9'\n"}])
    swapped = eng.swap(shadow, ["core/version.py"])
    assert swapped == ["core/version.py"]
    assert "VERSION = '9'" in (tmp_path / "core" / "version.py").read_text()


def test_diff_for(tmp_path):
    eng = EvolutionEngine(_fake_project(tmp_path))
    shadow = eng.create_shadow()
    eng.apply_changes(shadow, [{"path": "core/version.py", "content": "VERSION = 'Z'\n"}])
    old, new = eng.diff_for("core/version.py", shadow)
    assert 'VERSION = "1"' in old and "VERSION = 'Z'" in new


def test_propose_synthetic_bumps_marker():
    eng = EvolutionEngine(".")
    rationale, changes = eng.propose_synthetic()
    assert "Synthetic" in rationale
    assert changes[0]["path"] == "core/version.py"
    assert "EVOLUTION_COUNT" in changes[0]["content"]


class _FakeClient:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages, **kw):
        return self.reply


def test_propose_via_llm_parses_valid(tmp_path):
    reply = ('<<EVOLVE>>{"rationale": "tidy version", "files": '
             '[{"path": "core/version.py", "content": "VERSION = \\"x\\"\\n"}]}<<END>>')
    eng = EvolutionEngine(_fake_project(tmp_path), client=_FakeClient(reply))
    rationale, changes = eng.propose_via_llm(eng.discover())
    assert rationale == "tidy version"
    assert changes[0]["path"] == "core/version.py"


def test_propose_via_llm_rejects_bad_path(tmp_path):
    reply = '<<EVOLVE>>{"rationale": "x", "files": [{"path": "../evil.py", "content": "x"}]}<<END>>'
    eng = EvolutionEngine(_fake_project(tmp_path), client=_FakeClient(reply))
    with pytest.raises(EvolutionError):
        eng.propose_via_llm(eng.discover())


def test_propose_via_llm_no_block_raises(tmp_path):
    eng = EvolutionEngine(_fake_project(tmp_path), client=_FakeClient("I won't follow the format"))
    with pytest.raises(EvolutionError):
        eng.propose_via_llm(eng.discover())
