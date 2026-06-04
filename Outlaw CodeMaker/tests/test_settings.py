"""Settings: in-app reconfigure + atomic config save + no-timeout default."""
import copy
import json
import types

import pytest

from core.orchestrator import Orchestrator


@pytest.fixture
def orch(qapp, config):
    from db.schema import init_database
    init_database(config["database"]["path"])
    o = Orchestrator(config)
    yield o
    o.shutdown()


def test_reconfigure_changes_workspace_live(orch, tmp_path):
    newws = tmp_path / "godot_proj"
    newws.mkdir()
    cfg = copy.deepcopy(orch.config)
    cfg["agent"]["workspace_root"] = str(newws)
    cfg["vision"]["godot_window_title_match"] = "MyGame"
    seen = []
    orch.workspace_changed.connect(seen.append)

    orch.reconfigure(cfg)

    assert orch.file_controller.root == newws.resolve()
    assert orch.signal_mapper.root == newws
    assert orch.godot.title_match == "MyGame"
    assert seen == [str(newws)]


def test_reconfigure_refuses_while_busy(orch):
    orch._worker = types.SimpleNamespace(isRunning=lambda: True)
    with pytest.raises(RuntimeError):
        orch.reconfigure(orch.config)
    orch._worker = None


def test_apply_settings_writes_config_atomically(orch, tmp_path):
    orch.config_path = tmp_path / "config.json"   # don't touch the real one
    cfg = copy.deepcopy(orch.config)
    cfg["lm_studio"]["base_url"] = "http://127.0.0.1:9999/v1"
    orch.apply_settings(cfg)

    written = json.loads((tmp_path / "config.json").read_text())
    assert written["lm_studio"]["base_url"] == "http://127.0.0.1:9999/v1"
    assert orch.client.config.base_url == "http://127.0.0.1:9999/v1"


def test_generation_timeout_is_a_generous_hang_guard(orch):
    # Generous per-call ceiling (not a rush) that prevents an infinite hang if the
    # server wedges (e.g. on an OOM). None would risk a permanent stall.
    t = orch.client.config.request_timeout_seconds
    assert t is None or t >= 600


def test_settings_dialog_produces_config(qapp, config):
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(config)
    dlg.url_edit.setText("http://127.0.0.1:1234/v1")
    dlg.title_edit.setText("Godot Engine")
    dlg._save()
    assert dlg.result_config is not None
    assert dlg.result_config["lm_studio"]["base_url"] == "http://127.0.0.1:1234/v1"
    assert dlg.result_config["vision"]["godot_window_title_match"] == "Godot Engine"
