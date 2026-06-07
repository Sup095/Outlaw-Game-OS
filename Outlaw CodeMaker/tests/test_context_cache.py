"""Context cache (Phase 6) — store/hit/miss, TTL, eviction, corruption, namespacing."""
import json
import time

from core.context_cache import ContextCache, signature


def test_set_get_roundtrip(tmp_path):
    c = ContextCache(tmp_path, namespace="projA")
    assert c.get("k") is None                 # miss before set
    assert c.set("k", {"tree": ["a", "b"], "n": 3}) is True
    assert c.get("k") == {"tree": ["a", "b"], "n": 3}


def test_signature_changes_with_inputs():
    a = signature("/proj", 3, "mtime-1")
    b = signature("/proj", 3, "mtime-2")
    same = signature("/proj", 3, "mtime-1")
    assert a == same
    assert a != b
    assert len(a) == 32


def test_get_or_compute_only_computes_on_miss(tmp_path):
    c = ContextCache(tmp_path, namespace="projA")
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"v": 42}

    assert c.get_or_compute("key", compute) == {"v": 42}
    assert c.get_or_compute("key", compute) == {"v": 42}  # served from disk
    assert calls["n"] == 1                                 # computed exactly once


def test_ttl_expiry(tmp_path):
    c = ContextCache(tmp_path, namespace="projA")
    c.set("k", "val")
    assert c.get("k", ttl=100) == "val"     # fresh
    time.sleep(0.05)
    assert c.get("k", ttl=0.01) is None     # expired → miss (and removed)
    assert c.get("k") is None


def test_namespaces_are_isolated(tmp_path):
    a = ContextCache(tmp_path, namespace="game-one")
    b = ContextCache(tmp_path, namespace="game-two")
    a.set("shared-key", "A")
    b.set("shared-key", "B")
    assert a.get("shared-key") == "A"
    assert b.get("shared-key") == "B"


def test_corrupt_entry_is_a_clean_miss(tmp_path):
    c = ContextCache(tmp_path, namespace="projA")
    c.set("k", "ok")
    # Corrupt the underlying file.
    p = c._path("k")
    p.write_text("{ this is not valid json", encoding="utf-8")
    assert c.get("k") is None                # no exception, just a miss
    assert not p.exists()                     # corrupt file discarded


def test_non_serialisable_value_returns_false(tmp_path):
    c = ContextCache(tmp_path, namespace="projA")
    assert c.set("k", {1, 2, 3}) is False    # a set isn't JSON-serialisable
    assert c.get("k") is None


def test_size_cap_evicts_oldest(tmp_path):
    # Tiny cap so a few KB entries trigger eviction.
    c = ContextCache(tmp_path, namespace="projA", max_bytes=4000)
    big = "x" * 1500
    for i in range(10):
        c.set(f"key-{i}", big)
        time.sleep(0.01)  # distinct mtimes so eviction order is deterministic
    st = c.stats()
    assert st["bytes"] <= c.max_bytes        # stayed under the cap
    assert st["entries"] < 10                # something was evicted
    assert c.get("key-9") == big             # the newest survived


def test_clear_and_invalidate(tmp_path):
    c = ContextCache(tmp_path, namespace="projA")
    c.set("a", 1); c.set("b", 2)
    c.invalidate("a")
    assert c.get("a") is None and c.get("b") == 2
    removed = c.clear()
    assert removed >= 1 and c.get("b") is None
