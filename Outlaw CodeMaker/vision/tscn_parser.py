"""The .tscn Architect — read and surgically edit Godot scene files.

Godot `.tscn` is an INI-like text format:

    [gd_scene load_steps=3 format=3 uid="uid://abc"]

    [ext_resource type="Script" path="res://Player.gd" id="1_abc"]

    [sub_resource type="RectangleShape2D" id="RectangleShape2D_x"]
    size = Vector2(32, 48)

    [node name="Player" type="CharacterBody2D"]
    script = ExtResource("1_abc")

    [node name="Sprite2D" type="Sprite2D" parent="."]
    position = Vector2(0, -8)

    [connection signal="body_entered" from="." to="." method="_on_body_entered"]

Design choice: each block keeps its *raw body lines* verbatim, so parsing →
serializing is loss-less. Edits are surgical (append a node/connection, set one
property) rather than reformatting the whole file — this avoids corrupting
scenes that use features we don't model. All agent writes go through the diff
approval gate, so the user sees exactly what changes before it lands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


HEADER_RE = re.compile(r"^\[(\w+)([^\]]*)\]\s*$")
ATTR_RE = re.compile(r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|[^\s\]]+)')
PROP_RE = re.compile(r"^([A-Za-z_][\w/]*)\s*=\s*(.*)$")


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]
    return v


def _quote(v: str) -> str:
    return '"' + v.replace('"', '\\"') + '"'


@dataclass
class TscnBlock:
    type: str                      # gd_scene | ext_resource | sub_resource | node | connection | editable
    attrs: dict[str, str] = field(default_factory=dict)
    body: list[str] = field(default_factory=list)   # raw property lines, verbatim
    raw_header: str | None = None                    # original header line, if parsed

    def header_line(self) -> str:
        if self.raw_header is not None:
            return self.raw_header
        parts = [self.type]
        for k, v in self.attrs.items():
            # Numbers/known-bare keys stay bare; everything else is quoted.
            if k in {"load_steps", "format"} or re.fullmatch(r"-?\d+(\.\d+)?", str(v)):
                parts.append(f"{k}={v}")
            else:
                parts.append(f"{k}={_quote(v)}")
        return f"[{' '.join(parts)}]"

    def get_property(self, key: str) -> str | None:
        for line in self.body:
            m = PROP_RE.match(line)
            if m and m.group(1) == key:
                return m.group(2)
        return None

    def set_property(self, key: str, value: str) -> None:
        for i, line in enumerate(self.body):
            m = PROP_RE.match(line)
            if m and m.group(1) == key:
                self.body[i] = f"{key} = {value}"
                return
        self.body.append(f"{key} = {value}")


class TscnError(Exception):
    pass


class TscnDocument:
    def __init__(self, blocks: list[TscnBlock]):
        self.blocks = blocks

    # ----- parsing / serialization ---------------------------------------

    @classmethod
    def parse(cls, text: str) -> "TscnDocument":
        blocks: list[TscnBlock] = []
        current: TscnBlock | None = None
        for raw in text.splitlines():
            m = HEADER_RE.match(raw)
            if m:
                current = TscnBlock(
                    type=m.group(1),
                    attrs={k: _unquote(v) for k, v in ATTR_RE.findall(m.group(2))},
                    body=[],
                    raw_header=raw,
                )
                blocks.append(current)
            else:
                if current is None:
                    # Junk before the first header — keep as a synthetic preamble block.
                    if raw.strip():
                        current = TscnBlock(type="__preamble__", raw_header="")
                        blocks.append(current)
                        current.body.append(raw)
                    continue
                if raw.strip() == "":
                    continue  # blank separators are re-added on serialize
                current.body.append(raw)
        if not any(b.type == "gd_scene" for b in blocks):
            raise TscnError("Not a valid .tscn file (missing [gd_scene] header).")
        return cls(blocks)

    def serialize(self) -> str:
        out: list[str] = []
        for blk in self.blocks:
            if blk.type == "__preamble__":
                out.extend(blk.body)
                continue
            out.append(blk.header_line())
            out.extend(blk.body)
            out.append("")  # blank line between blocks (Godot style)
        text = "\n".join(out).rstrip("\n") + "\n"
        return text

    # ----- queries --------------------------------------------------------

    def _scene_header(self) -> TscnBlock | None:
        for b in self.blocks:
            if b.type == "gd_scene":
                return b
        return None

    def node_blocks(self) -> list[TscnBlock]:
        return [b for b in self.blocks if b.type == "node"]

    def find_node(self, name: str) -> TscnBlock | None:
        for b in self.node_blocks():
            if b.attrs.get("name") == name:
                return b
        return None

    def tree_summary(self) -> str:
        lines = ["Scene tree:"]
        for b in self.node_blocks():
            name = b.attrs.get("name", "?")
            ntype = b.attrs.get("type", "?")
            parent = b.attrs.get("parent", "")
            depth = 0 if parent in ("", ) else (1 if parent == "." else parent.count("/") + 2)
            indent = "  " * depth
            lines.append(f"{indent}- {name} : {ntype}")
        conns = [b for b in self.blocks if b.type == "connection"]
        if conns:
            lines.append("Connections:")
            for c in conns:
                lines.append(
                    f"  {c.attrs.get('signal')}  {c.attrs.get('from')} -> "
                    f"{c.attrs.get('to')}.{c.attrs.get('method')}"
                )
        ext = [b for b in self.blocks if b.type == "ext_resource"]
        if ext:
            lines.append("External resources:")
            for e in ext:
                lines.append(f"  {e.attrs.get('id')}: {e.attrs.get('type')} {e.attrs.get('path')}")
        return "\n".join(lines)

    # ----- surgical edits -------------------------------------------------

    def add_node(self, name: str, type: str, parent: str = ".", properties: dict | None = None) -> TscnBlock:
        if self.find_node(name):
            raise TscnError(f"node '{name}' already exists")
        attrs = {"name": name, "type": type}
        if parent:
            attrs["parent"] = parent
        blk = TscnBlock(type="node", attrs=attrs)
        for k, v in (properties or {}).items():
            blk.set_property(k, v)
        self.blocks.append(blk)
        return blk

    def set_node_property(self, node_name: str, key: str, value: str) -> None:
        node = self.find_node(node_name)
        if not node:
            raise TscnError(f"node '{node_name}' not found")
        node.set_property(key, value)

    def add_connection(self, signal: str, from_node: str, to_node: str, method: str) -> TscnBlock:
        blk = TscnBlock(
            type="connection",
            attrs={"signal": signal, "from": from_node, "to": to_node, "method": method},
        )
        self.blocks.append(blk)
        return blk

    def add_ext_resource(self, type: str, path: str, res_id: str | None = None) -> str:
        res_id = res_id or f"{len([b for b in self.blocks if b.type == 'ext_resource']) + 1}_outlaw"
        blk = TscnBlock(type="ext_resource", attrs={"type": type, "path": path, "id": res_id})
        # insert after the last ext_resource (or right after gd_scene header)
        idx = 0
        for i, b in enumerate(self.blocks):
            if b.type in ("gd_scene", "ext_resource"):
                idx = i + 1
        self.blocks.insert(idx, blk)
        self._recompute_load_steps()
        return res_id

    def _recompute_load_steps(self) -> None:
        header = self._scene_header()
        if not header:
            return
        n = sum(1 for b in self.blocks if b.type in ("ext_resource", "sub_resource")) + 1
        header.attrs["load_steps"] = str(n)
        if header.raw_header:
            header.raw_header = re.sub(r"load_steps=\d+", f"load_steps={n}", header.raw_header)
            if "load_steps" not in header.raw_header:
                header.raw_header = header.raw_header.replace("[gd_scene", f"[gd_scene load_steps={n}", 1)
