"""SpillPlan (Phase 6) — GPU offload + model hints by VRAM tier."""
from core.vram_saver import compute_spill_plan, SpillPlan, VRAMMode


def test_full_keeps_whole_model_on_gpu():
    p = compute_spill_plan(VRAMMode.FULL, {"vram_free_mb": 8000, "ram_total_mb": 32000})
    assert p.gpu_offload == 1.0
    assert p.prefer_cpu is False
    assert p.aggressive_cache is False


def test_lean_half_offload_and_caches():
    p = compute_spill_plan(VRAMMode.LEAN, {"vram_free_mb": 1200, "ram_total_mb": 16000})
    assert p.gpu_offload == 0.5
    assert p.prefer_cpu is False
    assert p.aggressive_cache is True


def test_minimal_tiny_vram_runs_on_cpu():
    p = compute_spill_plan(VRAMMode.MINIMAL, {"vram_free_mb": 400, "ram_total_mb": 16000})
    assert p.gpu_offload == 0.0
    assert p.prefer_cpu is True
    assert p.aggressive_cache is True


def test_minimal_offload_scales_with_free_vram():
    mid = compute_spill_plan(VRAMMode.MINIMAL, {"vram_free_mb": 1000})
    more = compute_spill_plan(VRAMMode.MINIMAL, {"vram_free_mb": 2000})
    assert mid.gpu_offload == 0.2
    assert more.gpu_offload == 0.35


def test_minimal_no_nvml_assumes_all_cpu():
    p = compute_spill_plan(VRAMMode.MINIMAL, {"vram_free_mb": None, "ram_total_mb": None})
    assert p.gpu_offload == 0.0
    assert p.prefer_cpu is True


def test_low_ram_warning_in_hint():
    p = compute_spill_plan(VRAMMode.MINIMAL, {"vram_free_mb": 400, "ram_total_mb": 4096})
    assert "under 8 GB RAM" in p.model_hint


def test_to_dict_is_json_friendly():
    p = compute_spill_plan(VRAMMode.LEAN, {"vram_free_mb": 1200})
    d = p.to_dict()
    assert set(d) == {"gpu_offload", "prefer_cpu", "aggressive_cache", "model_hint", "reason"}
    assert isinstance(p, SpillPlan)
