"""LM Studio tuning (Phase 6) — offload arg mapping, command, guidance, safe apply."""
from core.lmstudio_tuning import _gpu_arg, suggested_command, guidance_for, apply_offload
from core.vram_saver import compute_spill_plan, VRAMMode


def _minimal_plan():
    return compute_spill_plan(VRAMMode.MINIMAL, {"vram_free_mb": 400, "ram_total_mb": 16000})


def test_gpu_arg_mapping():
    assert _gpu_arg(0.0) == "off"
    assert _gpu_arg(1.0) == "max"
    assert _gpu_arg(0.5) == "0.50"
    assert _gpu_arg(None) == "off"      # bad input → safe default


def test_suggested_command():
    assert suggested_command(0.2, "qwen2.5-3b") == "lms load qwen2.5-3b --gpu 0.20 -y"
    assert suggested_command(1.0) == "lms load <your-model> --gpu max -y"


def test_guidance_includes_percent_and_command():
    g = guidance_for(_minimal_plan(), "qwen2.5-3b")
    assert "GPU offload: ~0%" in g
    assert "lms load qwen2.5-3b" in g


def test_apply_without_model_is_advisory_not_applied():
    r = apply_offload(_minimal_plan(), None)
    assert r["applied"] is False
    assert r["guidance"]
    assert r["command"].endswith("-y")
    # Always returns the full result contract.
    assert set(r) >= {"applied", "available", "command", "guidance", "message"}


def test_apply_never_raises():
    # Whether or not lms is installed, this must return a dict, never raise.
    r = apply_offload(_minimal_plan(), "definitely-not-a-real-model-xyz")
    assert isinstance(r, dict)
    assert r["applied"] in (True, False)
