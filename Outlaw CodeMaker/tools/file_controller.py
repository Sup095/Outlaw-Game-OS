"""File controller + Virtual Workspace Manager.

All file operations are sandboxed under workspace_root. Path traversal attempts
(`..`, absolute paths outside root) raise PermissionError before any I/O.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path


SAFE_TEXT_EXTS = {
    ".gd", ".tscn", ".tres", ".cfg", ".godot", ".import",
    ".py", ".pyw", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".md", ".txt",
    ".gdshader", ".shader",
}


class FileController:
    def __init__(self, workspace_root: str):
        self.root = Path(workspace_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ----- safety helpers -------------------------------------------------

    def _normalize_rel(self, rel: str) -> str:
        """Accept the path styles a model naturally uses for Godot.

        - 'res://scripts/Player.gd'  -> 'scripts/Player.gd' (res:// IS the project root)
        - backslashes -> forward slashes
        - strip leading slashes so '/Player.gd' is treated project-relative
        - reject 'user://' (that's runtime save data, not editable project files)
        """
        rel = (rel or "").strip().replace("\\", "/")
        low = rel.lower()
        if low.startswith("user://"):
            raise PermissionError(
                "user:// is runtime save data, not an editable project file. "
                "Use a project-relative path (res://...)."
            )
        if low.startswith("res://"):
            rel = rel[6:]
        return rel.lstrip("/")

    def _resolve(self, rel: str) -> Path:
        rel = self._normalize_rel(rel)
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {rel}") from exc
        return target

    def _is_text_safe(self, p: Path) -> bool:
        return p.suffix.lower() in SAFE_TEXT_EXTS

    # ----- CRUD -----------------------------------------------------------

    def read(self, path: str) -> str:
        p = self._resolve(path)
        if not p.exists():
            return f"ERROR: not found: {path}"
        if not p.is_file():
            return f"ERROR: not a file: {path}"
        if not self._is_text_safe(p):
            return f"ERROR: refusing to read binary/unknown type: {path}"
        return p.read_text(encoding="utf-8", errors="replace")

    def current_text(self, path: str) -> str:
        """Raw existing content, or '' if the file doesn't exist. For diffing."""
        p = self._resolve(path)
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
        return ""

    def read_lines(self, path: str, start: int, end: int) -> str:
        """Read a 1-indexed inclusive line range with line numbers (for big files)."""
        text = self.read(path)
        if text.startswith("ERROR:"):
            return text
        lines = text.splitlines()
        start = max(1, int(start))
        end = min(len(lines), int(end))
        if start > end:
            return "(empty range)"
        return "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, end))

    def write(self, path: str, content: str) -> str:
        from core.atomic import atomic_write
        p = self._resolve(path)
        atomic_write(p, content)  # .tmp + os.replace — corruption-safe
        return f"wrote {len(content)} bytes to {path}"

    def delete(self, path: str) -> str:
        p = self._resolve(path)
        if not p.exists():
            return f"already absent: {path}"
        if p.is_dir():
            for child in sorted(p.rglob("*"), reverse=True):
                child.unlink() if child.is_file() else child.rmdir()
            p.rmdir()
            return f"removed dir {path}"
        p.unlink()
        return f"removed file {path}"

    def mkdir(self, path: str) -> str:
        p = self._resolve(path)
        p.mkdir(parents=True, exist_ok=True)
        return f"created {path}"

    def search(self, pattern: str) -> str:
        matches: list[str] = []
        for p in self.root.rglob("*"):
            if p.is_file() and fnmatch.fnmatch(p.name, pattern):
                matches.append(str(p.relative_to(self.root)).replace("\\", "/"))
            if len(matches) >= 200:
                break
        return "\n".join(matches) if matches else "(no matches)"

    def search_text(self, query: str, regex: bool = False, max_results: int = 80) -> str:
        """Search file *contents* across the project — find every place a symbol,
        function, signal or node name appears, so the agent knows what to adapt.
        Returns 'path:line: text' matches (substring by default, or regex)."""
        import re as _re
        matcher = None
        if regex:
            try:
                matcher = _re.compile(query)
            except _re.error as exc:
                return f"ERROR: bad regex: {exc}"
        skip = {"__pycache__", "node_modules", ".git", ".godot", ".import"}
        out: list[str] = []
        for p in self.root.rglob("*"):
            if len(out) >= max_results:
                break
            if not p.is_file() or p.suffix.lower() not in SAFE_TEXT_EXTS:
                continue
            rel = p.relative_to(self.root)
            if any(part in skip for part in rel.parts):
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            rel_str = str(rel).replace("\\", "/")
            for i, line in enumerate(lines, start=1):
                hit = matcher.search(line) if matcher else (query in line)
                if hit:
                    out.append(f"{rel_str}:{i}: {line.strip()[:160]}")
                    if len(out) >= max_results:
                        break
        if not out:
            return f"(no matches for {query!r})"
        header = f"{len(out)} match(es)" + (" (truncated)" if len(out) >= max_results else "") + ":"
        return header + "\n" + "\n".join(out)

    # ----- Virtual Workspace Manager --------------------------------------

    def list_tree(self, depth: int = 4) -> str:
        lines: list[str] = [f"{self.root.name}/"]
        self._walk(self.root, prefix="  ", lines=lines, depth=depth, current=0)
        return "\n".join(lines)

    def tree_dict(self, depth: int = 6) -> dict:
        return self._build_dict(self.root, depth=depth, current=0)

    def _walk(self, node: Path, prefix: str, lines: list[str], depth: int, current: int) -> None:
        if current >= depth:
            return
        try:
            children = sorted(node.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in {"__pycache__", "node_modules"}:
                continue
            lines.append(f"{prefix}{child.name}{'/' if child.is_dir() else ''}")
            if child.is_dir():
                self._walk(child, prefix + "  ", lines, depth, current + 1)

    def _build_dict(self, node: Path, depth: int, current: int) -> dict:
        result: dict = {"name": node.name, "type": "dir" if node.is_dir() else "file"}
        if node.is_dir() and current < depth:
            result["children"] = []
            try:
                for child in sorted(node.iterdir()):
                    if child.name.startswith(".") or child.name in {"__pycache__", "node_modules"}:
                        continue
                    result["children"].append(self._build_dict(child, depth, current + 1))
            except PermissionError:
                pass
        return result

    def file_count(self) -> int:
        return sum(1 for p in self.root.rglob("*") if p.is_file())
