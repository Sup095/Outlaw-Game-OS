"""Read a Godot project's key facts from project.godot — gives the agent
instant orientation (name, main scene, engine, autoloads, input actions).

Also owns the Outlaw-managed sidecar at ``<project>/.outlaw/project.json``,
where we persist what the New Game wizard learned (dimension, genre, style,
mechanics) and what the Steam publish pipeline needs later (app id, depots,
target platforms). The sidecar is plain JSON so the user can edit it by hand.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def scaffold_files(name: str) -> list[tuple[str, str]]:
    """Return (relative_path, content) for a minimal, VALID Godot 4 project skeleton.

    Deterministic boilerplate (not LLM-generated) so a from-scratch project always
    opens in Godot. The agent then builds features on top of it.
    """
    safe = name.strip() or "My Game"
    project_godot = (
        "; Engine configuration for Outlaw CodeMaker\n"
        "config_version=5\n\n"
        "[application]\n\n"
        f'config/name="{safe}"\n'
        'run/main_scene="res://Main.tscn"\n'
        'config/features=PackedStringArray("4.3", "Forward Plus")\n\n'
        "[rendering]\n\n"
        'renderer/rendering_method="gl_compatibility"\n'
    )
    main_tscn = (
        '[gd_scene load_steps=2 format=3 uid="uid://outlaw_main"]\n\n'
        '[ext_resource type="Script" path="res://Main.gd" id="1_main"]\n\n'
        '[node name="Main" type="Node2D"]\n'
        'script = ExtResource("1_main")\n'
    )
    main_gd = (
        "extends Node2D\n\n\n"
        "func _ready() -> void:\n"
        f'\tprint("{safe} running")\n'
    )
    return [
        ("project.godot", project_godot),
        ("Main.tscn", main_tscn),
        ("Main.gd", main_gd),
    ]


def read_project_info(workspace_root: str) -> str:
    root = Path(workspace_root)
    proj = root / "project.godot"
    if not proj.exists():
        return (
            "No project.godot in this folder — it's not a Godot project root. "
            "Set the project folder in Settings (Ctrl+,) if you want to edit a game."
        )
    text = proj.read_text(encoding="utf-8", errors="replace")

    name = re.search(r'config/name\s*=\s*"([^"]*)"', text)
    main_scene = re.search(r'run/main_scene\s*=\s*"([^"]*)"', text)
    features = re.search(r"config/features\s*=\s*PackedStringArray\(([^)]*)\)", text)

    autoloads: list[str] = []
    input_actions: list[str] = []
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            section = s.lower()
            continue
        if "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if section.startswith("[autoload"):
            autoloads.append(key)
        elif section.startswith("[input"):
            input_actions.append(key)

    gd = sum(1 for _ in root.rglob("*.gd"))
    tscn = sum(1 for _ in root.rglob("*.tscn"))

    out = [f"Godot project: {name.group(1) if name else '(unnamed)'}"]
    if main_scene:
        out.append(f"Main scene: {main_scene.group(1)}")
    if features:
        out.append(f"Engine/features: {features.group(1)}")
    out.append(f"Scripts: {gd} .gd  ·  Scenes: {tscn} .tscn")
    if autoloads:
        out.append("Autoload singletons: " + ", ".join(autoloads))
    if input_actions:
        shown = ", ".join(input_actions[:24])
        more = "" if len(input_actions) <= 24 else f" (+{len(input_actions) - 24} more)"
        out.append(f"Input actions: {shown}{more}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Outlaw-managed sidecar metadata
# ---------------------------------------------------------------------------
#
# Lives at <project>/.outlaw/project.json. Owned by Outlaw, but plain JSON so
# the user can hand-edit if they need to. The wizard writes the design fields
# (name, dimension, genre, style, mechanics); the Steam pipeline writes the
# publishing fields (steam_app_id, depots, target_platforms, build_dir) later.


METADATA_DIR = ".outlaw"
METADATA_FILE = "project.json"


def outlaw_metadata_path(project_root: str | Path) -> Path:
    """Resolve the sidecar path for a Godot project root."""
    return Path(project_root) / METADATA_DIR / METADATA_FILE


def read_outlaw_metadata(project_root: str | Path) -> dict | None:
    """Read the Outlaw metadata sidecar, or None if absent / unreadable."""
    p = outlaw_metadata_path(project_root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_outlaw_metadata(project_root: str | Path, data: dict) -> Path:
    """Merge ``data`` into the sidecar at ``<project>/.outlaw/project.json``.

    Merge (not replace) so a later writer — e.g. the Steam pipeline saving an
    app id — can't blow away earlier design fields the wizard wrote.
    """
    p = outlaw_metadata_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = read_outlaw_metadata(project_root) or {}
    existing.update(data)
    p.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return p
