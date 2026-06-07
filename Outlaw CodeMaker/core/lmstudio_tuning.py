"""LM Studio GPU-offload tuning (Phase 6).

The model itself is hosted by LM Studio, so how much of it lives in VRAM vs.
spills into system RAM is an LM Studio setting (its "GPU offload" slider, or
``lms load <model> --gpu <fraction>`` on the CLI). This module turns a
:class:`core.vram_saver.SpillPlan` into action:

  * If the ``lms`` CLI is available AND a model name is supplied, it will
    best-effort load that model with the recommended GPU-offload fraction.
  * Otherwise it returns clear, copy-pasteable guidance telling the user
    exactly what to set in LM Studio.

It never raises on failure and never blocks the agent — tuning is advisory.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def lms_available() -> bool:
    """True if LM Studio's ``lms`` CLI is on PATH."""
    return shutil.which("lms") is not None


def _gpu_arg(gpu_offload: float) -> str:
    """Map a 0.0–1.0 offload fraction to the ``lms load --gpu`` argument.

    LM Studio accepts ``off`` (all CPU/RAM), ``max`` (all GPU), or a 0–1 ratio.
    """
    try:
        f = float(gpu_offload)
    except (TypeError, ValueError):
        return "off"
    if f <= 0.0:
        return "off"
    if f >= 1.0:
        return "max"
    return f"{f:.2f}"


def suggested_command(gpu_offload: float, model: Optional[str] = None) -> str:
    """The exact ``lms`` command the user could run for this offload."""
    target = model or "<your-model>"
    return f"lms load {target} --gpu {_gpu_arg(gpu_offload)} -y"


def guidance_for(plan, model: Optional[str] = None) -> str:
    """Human-readable instructions for applying a SpillPlan in LM Studio."""
    pct = max(0, min(100, round(float(plan.gpu_offload) * 100)))
    lines = [
        f"Recommended model: {plan.model_hint}",
        f"GPU offload: ~{pct}%  (the rest runs on the CPU / system RAM)",
        f"  • In the LM Studio app: set the model's 'GPU Offload' slider to ~{pct}%.",
        f"  • Or on the command line: {suggested_command(plan.gpu_offload, model)}",
    ]
    if plan.prefer_cpu:
        lines.append("  • Low-VRAM mode: prefer a small model; expect slower but "
                     "more reliable generation.")
    return "\n".join(lines)


def apply_offload(plan, model: Optional[str] = None, timeout: int = 120) -> dict:
    """Best-effort apply the plan's GPU offload via the ``lms`` CLI.

    Returns a result dict:
        {
          "applied": bool,      # did we actually run a load?
          "available": bool,    # is the lms CLI present?
          "command": str,       # the command we ran / suggest
          "guidance": str,      # what to do (always set)
          "message": str,       # short status / error
        }

    We only auto-load when both the CLI is present and a concrete model name is
    given — we won't guess which model to load. Everything else is advisory.
    """
    available = lms_available()
    command = suggested_command(plan.gpu_offload, model)
    guidance = guidance_for(plan, model)

    if not available:
        return {
            "applied": False, "available": False, "command": command,
            "guidance": guidance,
            "message": "LM Studio CLI (lms) not found — apply the settings above manually.",
        }

    if not model:
        return {
            "applied": False, "available": True, "command": command,
            "guidance": guidance,
            "message": "Set a model name to auto-apply, or run the command above.",
        }

    # Best-effort load. Never let a CLI hiccup propagate.
    try:
        proc = subprocess.run(
            ["lms", "load", model, "--gpu", _gpu_arg(plan.gpu_offload), "-y"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("lms load failed to run: %s", e)
        return {
            "applied": False, "available": True, "command": command,
            "guidance": guidance, "message": f"Could not run lms: {e}",
        }

    if proc.returncode == 0:
        return {
            "applied": True, "available": True, "command": command,
            "guidance": guidance,
            "message": f"Loaded {model} at ~{round(plan.gpu_offload * 100)}% GPU offload.",
        }
    return {
        "applied": False, "available": True, "command": command,
        "guidance": guidance,
        "message": (proc.stderr or proc.stdout or f"lms exited {proc.returncode}").strip()[-300:],
    }
