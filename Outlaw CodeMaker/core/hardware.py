"""Hardware Awareness — real-time VRAM/RAM, with OOM-avoidance hints.

VRAM via NVIDIA's NVML (pynvml) when present, else via the Linux DRM sysfs
counters (AMD amdgpu + some Intel discrete GPUs expose
``mem_info_vram_total`` / ``mem_info_vram_used``), so non-NVIDIA machines get
real VRAM numbers too. RAM via psutil. Everything degrades gracefully: with no
readable GPU counters, VRAM reads as unavailable and the app keeps working
(the VRAM saver then tiers on available RAM instead).

Why it matters here: a loaded model already fills your VRAM. We can't resize
LM Studio's loaded KV-cache remotely, but we CAN shrink the `max_tokens` we
*request* when free VRAM is low — fewer generated tokens means a smaller peak
KV cache, which is the usual trigger for an OOM mid-generation.
`suggest_max_tokens()` does that.
"""

from __future__ import annotations

import glob
import logging
import os

logger = logging.getLogger(__name__)

# Friendlier GPU names keyed by the DRM driver in the device's uevent.
_DRM_DRIVER_NAMES = {
    "amdgpu": "AMD GPU",
    "radeon": "AMD GPU",
    "i915": "Intel GPU",
    "xe": "Intel GPU",
}


def _mb(byts: int) -> int:
    return int(byts / (1024 * 1024))


class HardwareMonitor:
    def __init__(self):
        self._nvml_ok = False
        self._handle = None
        self._gpu_name = ""
        self._drm_dev: str | None = None
        self._init_nvml()
        if not self._nvml_ok:
            self._init_drm_sysfs()
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

    def _init_drm_sysfs(self) -> None:
        """Find an AMD/Intel GPU with DRM VRAM counters (Linux only).

        Two sysfs conventions:
          * amdgpu:       <card>/device/mem_info_vram_total + mem_info_vram_used
          * Intel (i915): <card>/lmem_total_bytes + lmem_avail_bytes
        Picks the card with the largest VRAM total (the discrete GPU on hybrid
        laptops). Integrated GPUs share system RAM and expose neither, so
        they're correctly skipped — the RAM-tier fallback covers them.
        Stores (kind, dir) so snapshot() knows which counters to read.
        """
        self._drm_kind = ""
        try:
            best: tuple[str, str, int] | None = None   # (kind, dir, total)
            for path in glob.glob("/sys/class/drm/card*/device/mem_info_vram_total"):
                try:
                    total = int(open(path).read().strip())
                except (OSError, ValueError):
                    continue
                if total > 0 and (best is None or total > best[2]):
                    best = ("amd", os.path.dirname(path), total)
            for path in glob.glob("/sys/class/drm/card*/lmem_total_bytes"):
                try:
                    total = int(open(path).read().strip())
                except (OSError, ValueError):
                    continue
                if total > 0 and (best is None or total > best[2]):
                    best = ("i915", os.path.dirname(path), total)
            if best is None:
                return
            self._drm_kind, self._drm_dev = best[0], best[1]
            driver = ""
            uevent = os.path.join(best[1] if best[0] == "amd" else os.path.join(best[1], "device"), "uevent")
            try:
                with open(uevent) as f:
                    for ln in f:
                        if ln.startswith("DRIVER="):
                            driver = ln.strip().split("=", 1)[1]
                            break
            except OSError:
                pass
            self._gpu_name = _DRM_DRIVER_NAMES.get(driver, "GPU") + (f" ({driver})" if driver else " (DRM)")
            logger.info("VRAM via DRM sysfs (%s): %s at %s (%d MB)",
                        self._drm_kind, self._gpu_name, self._drm_dev, best[2] // (1024 * 1024))
        except Exception as exc:  # noqa: BLE001 — never let a probe break startup
            logger.info("DRM sysfs VRAM probe unavailable: %s", exc)
            self._drm_dev = None
            self._drm_kind = ""

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
        elif self._drm_dev:
            # AMD/Intel: live byte counters from the DRM sysfs node.
            try:
                if self._drm_kind == "i915":
                    # Intel local memory: total + AVAILABLE (free comes directly).
                    with open(os.path.join(self._drm_dev, "lmem_total_bytes")) as f:
                        total = int(f.read().strip())
                    with open(os.path.join(self._drm_dev, "lmem_avail_bytes")) as f:
                        free = int(f.read().strip())
                    used = max(0, total - free)
                else:
                    # amdgpu: total + USED.
                    with open(os.path.join(self._drm_dev, "mem_info_vram_total")) as f:
                        total = int(f.read().strip())
                    with open(os.path.join(self._drm_dev, "mem_info_vram_used")) as f:
                        used = int(f.read().strip())
                    free = max(0, total - used)
                if total > 0:
                    snap["vram_used_mb"] = _mb(used)
                    snap["vram_total_mb"] = _mb(total)
                    snap["vram_free_mb"] = _mb(free)
                    snap["vram_pct"] = round(used / total * 100, 1)
            except (OSError, ValueError):
                pass  # transient read failure — this snapshot just has no VRAM
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
