"""Hardware Awareness — real-time VRAM/RAM, with OOM-avoidance hints.

VRAM via NVIDIA's NVML (pynvml); RAM via psutil. Both degrade gracefully: on a
non-NVIDIA box or if a library is missing, VRAM reads as unavailable and the app
keeps working.

Why it matters here: Qwen3 already fills your VRAM. We can't resize LM Studio's
loaded KV-cache remotely, but we CAN shrink the `max_tokens` we *request* when
free VRAM is low — fewer generated tokens means a smaller peak KV cache, which is
the usual trigger for an OOM mid-generation. `suggest_max_tokens()` does that.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _mb(byts: int) -> int:
    return int(byts / (1024 * 1024))


class HardwareMonitor:
    def __init__(self):
        self._nvml_ok = False
        self._handle = None
        self._gpu_name = ""
        self._init_nvml()
        try:
            import psutil  # noqa: F401
            self._psutil_ok = True
        except Exception:  # noqa: BLE001
            self._psutil_ok = False

    def _init_nvml(self) -> None:
        try:
            import pynvml
            pynvml.nvmlInit()
            self._pynvml = pynvml
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(self._handle)
            self._gpu_name = name.decode() if isinstance(name, bytes) else str(name)
            self._nvml_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.info("NVML unavailable (non-NVIDIA or driver missing): %s", exc)
            self._nvml_ok = False

    def snapshot(self) -> dict:
        snap: dict = {
            "gpu_name": self._gpu_name,
            "vram_used_mb": None, "vram_total_mb": None,
            "vram_free_mb": None, "vram_pct": None,
            "ram_used_mb": None, "ram_total_mb": None, "ram_pct": None,
        }
        if self._nvml_ok:
            try:
                mem = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
                snap["vram_used_mb"] = _mb(mem.used)
                snap["vram_total_mb"] = _mb(mem.total)
                snap["vram_free_mb"] = _mb(mem.free)
                snap["vram_pct"] = round(mem.used / mem.total * 100, 1) if mem.total else None
            except Exception:  # noqa: BLE001
                self._nvml_ok = False
        if self._psutil_ok:
            try:
                import psutil
                vm = psutil.virtual_memory()
                snap["ram_used_mb"] = _mb(vm.total - vm.available)
                snap["ram_total_mb"] = _mb(vm.total)
                snap["ram_pct"] = vm.percent
            except Exception:  # noqa: BLE001
                pass
        return snap

    def suggest_max_tokens(self, default: int) -> int:
        """Clamp requested output tokens when free VRAM is low (smaller peak KV cache).

        Graduated so a generation never tries to grow a KV cache bigger than the
        free VRAM can hold — the usual cause of an OOM crash mid-write.
        """
        snap = self.snapshot()
        free = snap["vram_free_mb"]
        if free is None:
            return default
        if free < 600:
            return min(default, 768)
        if free < 1200:
            return min(default, 1024)
        if free < 2000:
            return min(default, 1536)
        return default

    def format_line(self) -> str:
        s = self.snapshot()
        parts = []
        if s["vram_total_mb"]:
            parts.append(
                f"VRAM {s['vram_used_mb']/1024:.1f}/{s['vram_total_mb']/1024:.1f}G "
                f"({s['vram_pct']:.0f}%)"
            )
        else:
            parts.append("VRAM n/a")
        if s["ram_total_mb"]:
            parts.append(f"RAM {s['ram_used_mb']/1024:.0f}/{s['ram_total_mb']/1024:.0f}G")
        return " · ".join(parts)

    def vram_critical(self, threshold_mb: int = 400) -> bool:
        free = self.snapshot()["vram_free_mb"]
        return free is not None and free < threshold_mb

    def close(self) -> None:
        if self._nvml_ok:
            try:
                self._pynvml.nvmlShutdown()
            except Exception:  # noqa: BLE001
                pass
