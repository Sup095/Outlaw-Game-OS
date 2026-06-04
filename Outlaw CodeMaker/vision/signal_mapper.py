"""Signal-Bus Mapper — map a Godot project's signals and singletons.

Builds a dependency picture so the agent (and you) can see what's wired to what
before touching game logic:
  - `signal foo(args)` declarations, per script
  - `.connect(...)` / `emit_signal(...)` / `foo.emit(...)` usages
  - Autoload singletons from project.godot's [autoload] section

Pure text scanning — fast, no Godot runtime needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


SIGNAL_DECL_RE = re.compile(r"^\s*signal\s+(\w+)", re.MULTILINE)
EMIT_RE = re.compile(r'emit_signal\(\s*["\'](\w+)["\']|(\b\w+)\.emit\(')
CONNECT_RE = re.compile(r'\.connect\(|connect\(\s*["\'](\w+)["\']')


@dataclass
class ScriptInfo:
    path: str
    signals: list[str] = field(default_factory=list)
    emits: list[str] = field(default_factory=list)
    connect_count: int = 0


@dataclass
class SignalMap:
    scripts: list[ScriptInfo] = field(default_factory=list)
    autoloads: dict[str, str] = field(default_factory=dict)

    @property
    def total_signals(self) -> int:
        return sum(len(s.signals) for s in self.scripts)

    def summary(self) -> str:
        lines = [
            f"Signal-Bus Map — {len(self.scripts)} script(s), "
            f"{self.total_signals} signal(s), {len(self.autoloads)} singleton(s)",
            "",
        ]
        if self.autoloads:
            lines.append("Singletons (autoload):")
            for name, path in self.autoloads.items():
                lines.append(f"  • {name}  →  {path}")
            lines.append("")
        decl = [s for s in self.scripts if s.signals]
        if decl:
            lines.append("Declared signals:")
            for s in decl:
                lines.append(f"  {s.path}")
                for sig in s.signals:
                    lines.append(f"      signal {sig}")
            lines.append("")
        emitters = [s for s in self.scripts if s.emits]
        if emitters:
            lines.append("Emitters:")
            for s in emitters:
                uniq = sorted(set(s.emits))
                lines.append(f"  {s.path}: {', '.join(uniq)}")
        if not decl and not self.autoloads:
            lines.append("(No signals or autoloads found — is this a Godot project root?)")
        return "\n".join(lines)


class SignalMapper:
    def __init__(self, workspace_root: str):
        self.root = Path(workspace_root)

    def scan(self) -> SignalMap:
        smap = SignalMap()
        smap.autoloads = self._scan_autoloads()
        if self.root.exists():
            for gd in sorted(self.root.rglob("*.gd")):
                if any(part in {".godot", "addons"} for part in gd.parts):
                    continue
                try:
                    text = gd.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = str(gd.relative_to(self.root)).replace("\\", "/")
                info = ScriptInfo(path=rel)
                info.signals = SIGNAL_DECL_RE.findall(text)
                for m in EMIT_RE.finditer(text):
                    name = m.group(1) or m.group(2)
                    if name:
                        info.emits.append(name)
                info.connect_count = len(CONNECT_RE.findall(text))
                if info.signals or info.emits or info.connect_count:
                    smap.scripts.append(info)
        return smap

    def _scan_autoloads(self) -> dict[str, str]:
        proj = self.root / "project.godot"
        if not proj.exists():
            return {}
        autoloads: dict[str, str] = {}
        in_section = False
        try:
            for line in proj.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("["):
                    in_section = stripped.lower().startswith("[autoload")
                    continue
                if in_section and "=" in stripped:
                    name, _, path = stripped.partition("=")
                    autoloads[name.strip()] = path.strip().strip('"').lstrip("*")
        except OSError:
            pass
        return autoloads
