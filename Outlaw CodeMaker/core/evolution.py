"""The Phoenix Protocol — a safe, recursive self-upgrade loop.

Pipeline (every cycle):
    anchor()        git snapshot + .zip backup  ← restore point BEFORE anything
       │
    discover()      scan /core /ui /vision … → manifest + heuristics + bundle
       │
    propose()       synthetic (deterministic) OR LLM (Qwen3 returns full files)
       │
    create_shadow() copy the whole project to .outlaw_shadow/ (live files untouched)
       │
    apply_changes() atomic-write proposed files INTO the shadow only
       │
    validate()      py_compile every .py  +  headless offscreen init of the app
       │
    (approve)       user reviews diff + validation report  ← approve-before-swap
       │
    swap()          atomic-replace ONLY changed live files from the shadow → restart

The live tree is never touched until validation passes AND the user approves.
If a swapped build still fails to launch 3× in a row, boot_restore.py git-resets.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from .atomic import atomic_write


logger = logging.getLogger(__name__)

# Directories the protocol is allowed to read for discovery and rewrite.
EVOLVE_DIRS = ("core", "ui", "vision", "db", "tools")
# Extra single files that may be rewritten.
EVOLVE_ROOT_FILES = ("main.py",)

IGNORE_NAMES = {
    ".venv", ".git", "__pycache__", ".outlaw_shadow", "backups",
    ".pytest_cache", "workspace", "tesseract", ".idea", ".vscode",
}
IGNORE_SUFFIXES = {".db", ".db-journal", ".zip", ".bak", ".tmp", ".png", ".jpg", ".exe"}

EVOLVE_PATTERN = re.compile(r"<<EVOLVE>>(.*?)<<END>>", re.DOTALL)


def _ignored(rel: Path) -> bool:
    if any(part in IGNORE_NAMES for part in rel.parts):
        return True
    return rel.suffix.lower() in IGNORE_SUFFIXES


def _iter_py(root: Path):
    for p in root.rglob("*.py"):
        rel = p.relative_to(root)
        if not _ignored(rel):
            yield p


class EvolutionError(Exception):
    pass


class EvolutionEngine:
    """Stateless-ish engine; safe to call from a worker thread."""

    def __init__(self, project_root: str | Path, client=None):
        self.root = Path(project_root).resolve()
        self.shadow = self.root / ".outlaw_shadow"
        self.backups = self.root / "backups"
        self.client = client

    # ---- path safety -----------------------------------------------------

    def _is_allowed_target(self, rel: str) -> bool:
        rel = rel.replace("\\", "/")
        if ".." in rel or rel.startswith("/"):
            return False
        if not rel.endswith(".py"):
            return False
        if rel in EVOLVE_ROOT_FILES:
            return True
        return rel.split("/", 1)[0] in EVOLVE_DIRS

    # ---- discovery -------------------------------------------------------

    def discover(self, bundle_budget: int = 12000) -> dict:
        manifest = []
        for d in EVOLVE_DIRS:
            base = self.root / d
            if not base.exists():
                continue
            for p in sorted(_iter_py(base)):
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = str(p.relative_to(self.root)).replace("\\", "/")
                manifest.append({
                    "path": rel,
                    "lines": text.count("\n") + 1,
                    "bytes": len(text),
                    "has_doc": text.lstrip().startswith(('"""', "'''")),
                    "todos": text.count("TODO") + text.count("FIXME"),
                })

        report_lines = [f"{len(manifest)} Python modules across {', '.join(EVOLVE_DIRS)}:"]
        for m in sorted(manifest, key=lambda x: -x["bytes"])[:15]:
            flag = "" if m["has_doc"] else "  (no module docstring)"
            todo = f"  TODO×{m['todos']}" if m["todos"] else ""
            report_lines.append(f"  {m['path']}  {m['lines']}L{flag}{todo}")

        # Build an LLM bundle: smallest whole files first, until budget.
        bundle: list[dict] = []
        used = 0
        for m in sorted(manifest, key=lambda x: x["bytes"]):
            if m["bytes"] > 6000:
                continue
            content = (self.root / m["path"]).read_text(encoding="utf-8", errors="replace")
            if used + len(content) > bundle_budget:
                continue
            bundle.append({"path": m["path"], "content": content})
            used += len(content)

        return {"manifest": manifest, "report": "\n".join(report_lines), "bundle": bundle}

    # ---- proposals -------------------------------------------------------

    def propose_synthetic(self) -> tuple[str, list[dict]]:
        """Deterministic, zero-risk change: bump core/version.py. Proves the pipeline."""
        from . import version as v
        new_count = int(getattr(v, "EVOLUTION_COUNT", 0)) + 1
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        content = (
            '"""Version + evolution marker.\n\n'
            "This file doubles as the safe target for the *synthetic* dry-run evolution:\n"
            "the dry-run bumps EVOLUTION_COUNT and BUILD, proving the shadow→validate→swap\n"
            "pipeline end-to-end with zero risk of breaking real logic.\n"
            '"""\n\n'
            f'VERSION = "{getattr(v, "VERSION", "3.0.0")}"\n'
            f"EVOLUTION_COUNT = {new_count}\n"
            f'BUILD = "{ts}"\n'
        )
        rationale = f"Synthetic dry-run: bump evolution marker to #{new_count} ({ts})."
        return rationale, [{"path": "core/version.py", "content": content}]

    def propose_via_llm(self, discovery: dict, on_log=None) -> tuple[str, list[dict]]:
        if self.client is None:
            raise EvolutionError("No LM Studio client available for LLM evolution.")
        from .api_client import ChatMessage
        from .orchestrator import strip_think

        tree = "\n".join(f"- {m['path']} ({m['lines']}L)" for m in discovery["manifest"])
        files_block = "\n\n".join(
            f"### FILE: {b['path']}\n```python\n{b['content']}\n```" for b in discovery["bundle"]
        )
        prompt = (
            "You are the Evolution Engine for Outlaw CodeMaker, improving your OWN source.\n"
            "Pick ONE high-impact, LOW-RISK improvement (clarity, a small Godot-helpful "
            "feature, a bug fix, or a docstring/typing pass). You may only rewrite files "
            "whose FULL content is shown below, and at most 2 of them. Return COMPLETE file "
            "contents (not diffs). The code MUST import and compile.\n\n"
            f"## Project modules\n{tree}\n\n## Editable files (full content)\n{files_block}\n\n"
            "Respond in EXACTLY this format and nothing else:\n"
            '<<EVOLVE>>{"rationale": "one sentence", "files": '
            '[{"path": "core/xxx.py", "content": "<entire new file>"}]}<<END>>'
        )
        if on_log:
            on_log("Sending codebase bundle to the model…")
        raw = self.client.complete([ChatMessage(role="user", content=prompt)],
                                    temperature=0.4, max_tokens=4096)
        raw = strip_think(raw)
        m = EVOLVE_PATTERN.search(raw)
        if not m:
            raise EvolutionError("Model did not return a valid <<EVOLVE>> block.")
        try:
            payload = json.loads(m.group(1).strip())
        except json.JSONDecodeError as exc:
            raise EvolutionError(f"Proposal JSON was malformed: {exc}")

        files = payload.get("files") or []
        changes: list[dict] = []
        for f in files[:2]:
            path, content = f.get("path"), f.get("content")
            if not path or content is None:
                continue
            if not self._is_allowed_target(path):
                raise EvolutionError(f"Proposal targets a disallowed path: {path}")
            changes.append({"path": path.replace("\\", "/"), "content": content})
        if not changes:
            raise EvolutionError("Proposal contained no valid file changes.")
        return payload.get("rationale", "(no rationale)"), changes

    # ---- shadow build ----------------------------------------------------

    def create_shadow(self) -> Path:
        if self.shadow.exists():
            shutil.rmtree(self.shadow, ignore_errors=True)

        def _ignore(dir_path, names):
            return {
                n for n in names
                if n in IGNORE_NAMES or Path(n).suffix.lower() in IGNORE_SUFFIXES
            }

        shutil.copytree(self.root, self.shadow, ignore=_ignore, dirs_exist_ok=True)
        return self.shadow

    def apply_changes(self, shadow: Path, changes: list[dict]) -> list[str]:
        applied = []
        for ch in changes:
            rel = ch["path"].replace("\\", "/")
            if not self._is_allowed_target(rel):
                raise EvolutionError(f"Refusing to write disallowed path: {rel}")
            atomic_write(shadow / rel, ch["content"])
            applied.append(rel)
        return applied

    # ---- validation ------------------------------------------------------

    _VALIDATE_HARNESS = '''\
import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import main
    from PyQt6.QtWidgets import QApplication
    from ui.styles import apply_dark_theme
    from core.orchestrator import Orchestrator
    from ui.main_window import MainWindow
    from db.schema import init_database
    cfg = main.load_config()
    td = tempfile.mkdtemp()
    cfg["database"]["path"] = os.path.join(td, "t.db")
    cfg["database"]["vault_path"] = os.path.join(td, "v.db")
    cfg["agent"]["workspace_root"] = os.path.join(td, "ws")
    init_database(cfg["database"]["path"])
    app = QApplication.instance() or QApplication([])
    apply_dark_theme(app, cfg["ui"])
    orch = Orchestrator(cfg)
    win = MainWindow(orch, cfg)
    win.show()
    orch.shutdown()
    print("VALIDATE_OK")
    sys.exit(0)
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
'''

    def validate(self, shadow: Path, on_log=None) -> tuple[bool, str]:
        log: list[str] = []

        # 1) compile every .py in the shadow
        py_files = [str(p) for p in _iter_py(shadow)]
        if on_log:
            on_log(f"Compiling {len(py_files)} modules…")
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", *py_files],
            cwd=str(shadow), capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            return False, "py_compile FAILED:\n" + (proc.stderr or proc.stdout)
        log.append(f"py_compile OK ({len(py_files)} files)")

        # 2) headless init of the whole app
        harness = shadow / "_evo_validate.py"
        atomic_write(harness, self._VALIDATE_HARNESS)
        if on_log:
            on_log("Headless init check (offscreen)…")
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        try:
            proc2 = subprocess.run(
                [sys.executable, "_evo_validate.py"],
                cwd=str(shadow), capture_output=True, text=True, timeout=300, env=env,
            )
        except subprocess.TimeoutExpired:
            return False, "\n".join(log) + "\nheadless init TIMED OUT"
        ok = proc2.returncode == 0 and "VALIDATE_OK" in proc2.stdout
        log.append("headless init OK" if ok else "headless init FAILED")
        if not ok:
            log.append((proc2.stdout or "") + "\n" + (proc2.stderr or ""))
        return ok, "\n".join(log)

    # ---- anchor (restore point) -----------------------------------------

    def anchor(self, on_log=None) -> str:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        notes = []
        # git snapshot
        # -c flags are per-invocation: identity for the commit, and safe.directory
        # so git works on drives that don't record ownership (e.g. F:). Never
        # mutates the user's global git config.
        git_cfg = ["-c", "safe.directory=*",
                   "-c", "user.name=Outlaw CodeMaker",
                   "-c", "user.email=outlaw@localhost"]
        try:
            subprocess.run(["git", *git_cfg, "add", "-A"], cwd=str(self.root),
                           capture_output=True, text=True, timeout=30)
            r = subprocess.run(
                ["git", *git_cfg, "commit", "-m", f"Pre-Upgrade Snapshot [{ts}]"],
                cwd=str(self.root), capture_output=True, text=True, timeout=30,
            )
            notes.append("git: committed snapshot" if r.returncode == 0
                         else "git: nothing to commit (clean)")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"git snapshot skipped: {exc}")
        # zip backup
        try:
            self.backups.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            zpath = self.backups / f"outlaw_{stamp}.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                for d in EVOLVE_DIRS:
                    for p in _iter_py(self.root / d) if (self.root / d).exists() else []:
                        zf.write(p, p.relative_to(self.root))
                for f in EVOLVE_ROOT_FILES + ("config.json",):
                    fp = self.root / f
                    if fp.exists():
                        zf.write(fp, f)
            notes.append(f"backup: {zpath.name}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"zip backup failed: {exc}")
        if on_log:
            on_log("Anchor: " + " · ".join(notes))
        return " · ".join(notes)

    # ---- swap ------------------------------------------------------------

    def diff_for(self, rel: str, shadow: Path) -> tuple[str, str]:
        live = self.root / rel
        old = live.read_text(encoding="utf-8", errors="replace") if live.exists() else ""
        new = (shadow / rel).read_text(encoding="utf-8", errors="replace")
        return old, new

    def swap(self, shadow: Path, changed: list[str]) -> list[str]:
        swapped = []
        for rel in changed:
            src = shadow / rel
            if not src.exists():
                continue
            atomic_write(self.root / rel, src.read_text(encoding="utf-8", errors="replace"))
            swapped.append(rel)
        return swapped


class EvolutionWorker(QThread):
    """Runs one evolution cycle off the UI thread."""

    status = pyqtSignal(str)
    log = pyqtSignal(str, str)
    candidate_ready = pyqtSignal(dict)   # {rationale, changed, validation_log, shadow_dir, dry_run}
    failed = pyqtSignal(str)
    cycle_done = pyqtSignal(bool)        # ok?

    def __init__(self, engine: EvolutionEngine, mode: str = "synthetic", dry_run: bool = True):
        super().__init__()
        self.engine = engine
        self.mode = mode
        self.dry_run = dry_run

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Evolution cycle crashed")
            self.failed.emit(str(exc))
            self.cycle_done.emit(False)

    def _emit_log(self, msg: str) -> None:
        self.log.emit("info", f"[evolution] {msg}")

    def _run(self) -> None:
        self.status.emit("Anchoring restore point…")
        self.engine.anchor(on_log=self._emit_log)

        if self.isInterruptionRequested():
            return
        self.status.emit("Scanning own codebase…")
        discovery = self.engine.discover()
        self._emit_log(discovery["report"].splitlines()[0])

        self.status.emit("Proposing improvement…")
        if self.mode == "llm":
            rationale, changes = self.engine.propose_via_llm(discovery, on_log=self._emit_log)
        else:
            rationale, changes = self.engine.propose_synthetic()
        self._emit_log(f"Proposal: {rationale}")

        if self.isInterruptionRequested():
            return
        self.status.emit("Building shadow copy…")
        shadow = self.engine.create_shadow()
        applied = self.engine.apply_changes(shadow, changes)
        self._emit_log(f"Applied to shadow: {', '.join(applied)}")

        self.status.emit("Validating shadow build…")
        ok, vlog = self.engine.validate(shadow, on_log=self._emit_log)
        self._emit_log(vlog.splitlines()[0] if vlog else "validation done")

        if not ok:
            self.failed.emit("Shadow build FAILED validation — live files untouched.\n" + vlog[:1500])
            self.cycle_done.emit(False)
            return

        self.status.emit("Validation passed — awaiting approval" if not self.dry_run
                         else "Dry-run passed — would swap (no live change)")
        self.candidate_ready.emit({
            "rationale": rationale,
            "changed": applied,
            "validation_log": vlog,
            "shadow_dir": str(shadow),
            "dry_run": self.dry_run,
        })
        self.cycle_done.emit(True)
