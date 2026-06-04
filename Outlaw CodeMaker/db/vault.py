"""The Vault — vector-lite RAG over the project, CPU/RAM only (no GPU, no model).

How it works:
  - Every text file is split into ~40-line overlapping chunks.
  - Each chunk → a fixed-size (1024-d) sparse "hashed term-frequency" vector,
    L2-normalised, stored as float32 BLOB in its own SQLite DB.
  - A query is vectorised the same way; cosine similarity (a single NumPy
    matrix-vector product) ranks the chunks. Top hits are injected into the
    agent's planning context (retrieval-augmented generation).

Why a hashing vectoriser and not embeddings: your VRAM is full with Qwen3.
This runs entirely on CPU in regular RAM, loads instantly, and is excellent at
surfacing the right code by symbol/keyword overlap — which is what matters for
"where is X defined / what touches Y" code retrieval.

Critical detail: token buckets use a STABLE hash (zlib.crc32), not Python's
built-in hash() which is salted per-process — otherwise vectors written in one
run wouldn't match queries in the next.
"""

from __future__ import annotations

import re
import sqlite3
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np

from tools.file_controller import SAFE_TEXT_EXTS


DIM = 1024
CHUNK_LINES = 40
CHUNK_OVERLAP = 10
MAX_FILE_BYTES = 400_000

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _expand(token: str) -> list[str]:
    """Token plus snake_case / camelCase sub-parts, to boost code recall."""
    out = {token}
    for part in token.split("_"):
        if len(part) >= 2:
            out.add(part)
    for part in CAMEL_RE.findall(token):
        if len(part) >= 2:
            out.add(part.lower())
    return list(out)


def vectorize(text: str) -> np.ndarray:
    vec = np.zeros(DIM, dtype=np.float32)
    for raw in TOKEN_RE.findall(text.lower()):
        for tok in _expand(raw):
            bucket = zlib.crc32(tok.encode("utf-8")) % DIM
            vec[bucket] += 1.0
    nz = vec > 0
    vec[nz] = 1.0 + np.log(vec[nz])          # sublinear tf
    norm = float(np.linalg.norm(vec))
    if norm > 0.0:
        vec /= norm
    return vec


def chunk_text(text: str, size: int = CHUNK_LINES, overlap: int = CHUNK_OVERLAP):
    lines = text.splitlines()
    if not lines:
        return
    step = max(1, size - overlap)
    i = 0
    while i < len(lines):
        window = lines[i : i + size]
        yield (i + 1, i + len(window), "\n".join(window))
        if i + size >= len(lines):
            break
        i += step


class Vault:
    def __init__(self, db_path: str, workspace_root: str):
        self.db_path = Path(db_path)
        self.root = Path(workspace_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    vector BLOB NOT NULL
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY, value TEXT
                )"""
            )

    # ----- indexing -------------------------------------------------------

    def reindex(self, progress_cb=None) -> int:
        """Rebuild the index from scratch. Returns the number of chunks indexed.

        progress_cb(done_files, total_files, path) is called as it goes (optional).
        """
        files = [
            p for p in self.root.rglob("*")
            if p.is_file()
            and p.suffix.lower() in SAFE_TEXT_EXTS
            and not any(part in {".godot", "__pycache__", "node_modules", ".git"} for part in p.parts)
        ]
        rows: list[tuple] = []
        total = len(files)
        for idx, p in enumerate(files, start=1):
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(p.relative_to(self.root)).replace("\\", "/")
            for start, end, chunk in chunk_text(text):
                if not chunk.strip():
                    continue
                vec = vectorize(f"{rel}\n{chunk}")
                rows.append((rel, start, end, chunk, vec.tobytes()))
            if progress_cb:
                progress_cb(idx, total, rel)

        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM chunks")
            c.executemany(
                "INSERT INTO chunks(path, start_line, end_line, text, vector) VALUES (?,?,?,?,?)",
                rows,
            )
            c.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('indexed_at', ?)",
                (datetime.now().isoformat(timespec="seconds"),),
            )
        return len(rows)

    # ----- search ---------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        qvec = vectorize(query)
        with sqlite3.connect(self.db_path) as c:
            rows = c.execute("SELECT id, vector FROM chunks").fetchall()
            if not rows:
                return []
            ids = np.array([r[0] for r in rows])
            mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), DIM)
            scores = mat @ qvec  # cosine (all vectors are unit-norm)
            k = min(top_k, len(rows))
            top = np.argpartition(-scores, k - 1)[:k]
            top = top[np.argsort(-scores[top])]
            results = []
            for i in top:
                if scores[i] <= 0.0:
                    continue
                cid = int(ids[i])
                row = c.execute(
                    "SELECT path, start_line, end_line, text FROM chunks WHERE id = ?", (cid,)
                ).fetchone()
                if row:
                    results.append({
                        "path": row[0], "start": row[1], "end": row[2],
                        "text": row[3], "score": round(float(scores[i]), 3),
                    })
            return results

    def search_context(self, query: str, top_k: int = 3, max_chars: int = 1800) -> str:
        """Compact RAG block for prompt injection."""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""
        out, used = ["## Relevant code from the Vault (RAG):"], 0
        for h in hits:
            block = f"\n### {h['path']} (lines {h['start']}-{h['end']}, score {h['score']})\n```\n{h['text']}\n```"
            if used + len(block) > max_chars:
                break
            out.append(block)
            used += len(block)
        return "\n".join(out)

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as c:
            n = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            files = c.execute("SELECT COUNT(DISTINCT path) FROM chunks").fetchone()[0]
            row = c.execute("SELECT value FROM meta WHERE key = 'indexed_at'").fetchone()
        return {"chunks": n, "files": files, "indexed_at": row[0] if row else None}
