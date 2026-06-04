"""The Vault — deterministic vectors + RAG retrieval (CPU, no model)."""
import numpy as np

from db.vault import Vault, chunk_text, vectorize


def test_vectorize_deterministic_and_unit_norm():
    v1 = vectorize("player jump velocity")
    v2 = vectorize("player jump velocity")
    assert np.allclose(v1, v2)
    assert abs(float(np.linalg.norm(v1)) - 1.0) < 1e-5


def test_related_text_scores_higher_than_unrelated():
    a = vectorize("func jump(): velocity.y = -400")
    related = vectorize("player jump velocity")
    unrelated = vectorize("sqlite database connection pool")
    assert float(a @ related) > float(a @ unrelated)


def test_chunking_windows():
    text = "\n".join(f"line{i}" for i in range(100))
    chunks = list(chunk_text(text, size=40, overlap=10))
    assert len(chunks) >= 2
    assert chunks[0][0] == 1  # first chunk starts at line 1


def test_reindex_and_search_finds_relevant_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "Player.gd").write_text("extends CharacterBody2D\nfunc jump():\n    velocity.y = -400\n")
    (ws / "Enemy.gd").write_text("extends Node\nfunc patrol():\n    move()\n")
    vault = Vault(str(tmp_path / "vault.db"), str(ws))
    n = vault.reindex()
    assert n >= 2
    hits = vault.search("jump velocity", top_k=3)
    assert hits
    assert hits[0]["path"] == "Player.gd"


def test_search_context_block(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.gd").write_text("func dash():\n    velocity.x = 600\n")
    vault = Vault(str(tmp_path / "v.db"), str(ws))
    vault.reindex()
    ctx = vault.search_context("dash", top_k=2)
    assert "Vault" in ctx and "a.gd" in ctx


def test_empty_index_returns_no_hits(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    vault = Vault(str(tmp_path / "v.db"), str(ws))
    vault.reindex()
    assert vault.search("anything") == []
    assert vault.stats()["chunks"] == 0
