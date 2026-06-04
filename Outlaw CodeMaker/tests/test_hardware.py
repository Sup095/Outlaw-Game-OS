"""Hardware awareness — snapshot shape, token clamp, graceful fallback."""
from core.hardware import HardwareMonitor


def test_snapshot_has_expected_keys():
    hw = HardwareMonitor()
    try:
        s = hw.snapshot()
        for k in ("vram_total_mb", "vram_free_mb", "ram_total_mb", "ram_pct", "gpu_name"):
            assert k in s
    finally:
        hw.close()


def test_ram_is_reported():
    hw = HardwareMonitor()
    try:
        s = hw.snapshot()
        # psutil should always give RAM on this machine
        assert s["ram_total_mb"] is None or s["ram_total_mb"] > 0
    finally:
        hw.close()


def test_suggest_max_tokens_bounds():
    hw = HardwareMonitor()
    try:
        out = hw.suggest_max_tokens(4096)
        assert 0 < out <= 4096
    finally:
        hw.close()


def test_format_line_nonempty():
    hw = HardwareMonitor()
    try:
        line = hw.format_line()
        assert "RAM" in line or "VRAM" in line
    finally:
        hw.close()
