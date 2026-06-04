"""Shared pytest fixtures. Runs Qt headless (offscreen) so tests need no display."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def config(tmp_path):
    base = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    base["database"]["path"] = str(tmp_path / "test.db")
    base["database"]["vault_path"] = str(tmp_path / "vault.db")  # isolate from real Vault
    base["agent"]["workspace_root"] = str(tmp_path / "ws")
    return base


@pytest.fixture
def store(config):
    from db.schema import init_database, MemoryStore
    init_database(config["database"]["path"])
    return MemoryStore(config["database"]["path"])
