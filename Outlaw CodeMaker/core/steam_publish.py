"""Steam publishing pipeline — wraps ``steamcmd`` to ship a Godot build to
Steamworks straight from Outlaw CodeMaker.

Design notes
------------
* **No credential storage.** We only ever persist the build account username.
  The first publish requires running ``steamcmd +login <user>`` interactively
  (Steam Guard / 2FA prompt). steamcmd caches the session for ~30 days, so
  subsequent calls succeed unattended. If the cache expires we surface a clear
  "needs interactive login" message instead of trying to scrape credentials.
* **Cross-platform.** Works on Windows (current dev machine) and Linux
  (Outlaw OS target). Detects the steamcmd binary via ``shutil.which`` first
  and falls back to common install paths.
* **Threaded.** ``SteamUploadWorker`` (QThread) runs the upload off the UI
  thread and streams output line-by-line via signals.
* **Per-project config.** Reads/writes ``<project>/.outlaw/project.json`` —
  the same sidecar the New Game wizard already creates.

The pipeline this file owns:
    1. Generate ``app_build.vdf`` + one ``depot_<id>.vdf`` per depot.
    2. Run ``steamcmd +login user +run_app_build app_build.vdf +quit``.
    3. Parse the BuildID out of the stream.
    4. (Optional) Promote the new BuildID to a branch via
       ``+app_build_set_live``.

The Steam config shape stored in ``.outlaw/project.json``::

    {
      "steam": {
        "app_id": "1234567",
        "build_account": "mygamedev",
        "default_branch": "default",
        "depots": [
          {
            "id": "1234568",
            "content_root": "build/windows",
            "exclusions": ["*.pdb", "*.log"]
          },
          { "id": "1234569", "content_root": "build/linux" }
        ]
      }
    }
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locating steamcmd
# ---------------------------------------------------------------------------


# Common install locations checked when `shutil.which` fails.
_WINDOWS_CANDIDATES = [
    r"C:\Program Files (x86)\Steam\steamcmd\steamcmd.exe",
    r"C:\Steam\steamcmd\steamcmd.exe",
    r"C:\steamcmd\steamcmd.exe",
    r"C:\steamworks_sdk\tools\ContentBuilder\builder\steamcmd.exe",
]

_POSIX_CANDIDATES = [
    "/usr/bin/steamcmd",
    "/usr/local/bin/steamcmd",
    "/usr/games/steamcmd",
    "/opt/steamcmd/steamcmd.sh",
    str(Path.home() / ".steam" / "steamcmd" / "steamcmd.sh"),
    str(Path.home() / "steamcmd" / "steamcmd.sh"),
]


def find_steamcmd(override: str | None = None) -> str | None:
    """Locate the steamcmd executable. Returns the absolute path, or None."""
    if override:
        p = Path(override).expanduser()
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
        # On Windows the .exe might be missing — try appending it.
        if sys.platform.startswith("win") and not str(p).lower().endswith(".exe"):
            p2 = p.with_suffix(".exe")
            if p2.exists():
                return str(p2)
        return None  # honored explicitly so user knows their override was wrong
    found = shutil.which("steamcmd") or shutil.which("steamcmd.exe")
    if found:
        return found
    candidates = _WINDOWS_CANDIDATES if sys.platform.startswith("win") else _POSIX_CANDIDATES
    for path in candidates:
        if Path(path).exists():
            return path
    return None


@dataclass
class SteamcmdStatus:
    available: bool
    path: str | None
    version: str | None = None
    note: str = ""


def check_steamcmd(override: str | None = None) -> SteamcmdStatus:
    """Detect steamcmd and capture its first-line banner if found."""
    path = find_steamcmd(override)
    if not path:
        return SteamcmdStatus(
            available=False, path=None,
            note="steamcmd not found. Install Steamworks SDK or pacman -S steamcmd, "
                 "then point Outlaw at it from the Steam panel if it's in a non-standard location.",
        )
    # Try to capture the version banner without doing a full login. `+quit` makes
    # steamcmd exit immediately after handshake.
    try:
        result = subprocess.run(
            [path, "+quit"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        text = (result.stdout or "") + (result.stderr or "")
        m = re.search(r"Steam Console Client.*?\(Built:[^)]+\)", text)
        version = m.group(0) if m else (text.splitlines()[0] if text else None)
        return SteamcmdStatus(available=True, path=path, version=version)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return SteamcmdStatus(
            available=False, path=path,
            note=f"steamcmd present at {path} but didn't respond: {exc}",
        )


# ---------------------------------------------------------------------------
# Login probe
# ---------------------------------------------------------------------------


@dataclass
class LoginStatus:
    logged_in: bool
    needs_interactive: bool
    username: str
    message: str


_LOGIN_OK_PATTERNS = [
    re.compile(r"Logged in OK", re.IGNORECASE),
    re.compile(r"Waiting for user info\.\.\.OK", re.IGNORECASE),
]
_LOGIN_NEEDS_2FA = [
    re.compile(r"Steam Guard code", re.IGNORECASE),
    re.compile(r"Mobile Authenticator", re.IGNORECASE),
    re.compile(r"Two-factor code", re.IGNORECASE),
    re.compile(r"password is required", re.IGNORECASE),
]


def check_login(
    steamcmd_path: str,
    username: str,
    timeout: int = 30,
) -> LoginStatus:
    """Try a cached-session login. Returns LoginStatus.

    Run ``steamcmd +login <user> +info +quit``. If steamcmd's session cache is
    still valid we get a clean "Logged in OK" without any prompts. If the
    cache expired, steamcmd prompts for password or Steam Guard, which we
    detect from output and flag as needs_interactive.
    """
    if not username:
        return LoginStatus(False, False, "", "No Steam build-account username set.")
    try:
        # Pipe an empty stdin so steamcmd won't actually wait on interactive
        # input — it will print the prompt, then EOF, then exit non-zero.
        proc = subprocess.run(
            [steamcmd_path, "+login", username, "+info", "+quit"],
            input="",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return LoginStatus(False, True, username,
                           "steamcmd timed out. The login cache may be expired — run "
                           f"`{steamcmd_path} +login {username}` from a terminal once, complete "
                           "the Steam Guard prompt, then come back.")
    except OSError as exc:
        return LoginStatus(False, False, username, f"Could not run steamcmd: {exc}")

    text = (proc.stdout or "") + (proc.stderr or "")
    if any(p.search(text) for p in _LOGIN_OK_PATTERNS):
        return LoginStatus(True, False, username, "Cached session is valid.")
    if any(p.search(text) for p in _LOGIN_NEEDS_2FA):
        return LoginStatus(False, True, username,
                           "Steam wants a password / Steam Guard code. Run "
                           f"`{steamcmd_path} +login {username}` from a terminal once, complete "
                           "the prompt, then retry from here.")
    # Unknown failure — return the last 200 chars so the user can see it.
    tail = text.strip().splitlines()[-6:] if text.strip() else []
    return LoginStatus(False, True, username,
                       "Login could not be verified. Last output:\n" + "\n".join(tail))


# ---------------------------------------------------------------------------
# VDF generation
# ---------------------------------------------------------------------------


@dataclass
class DepotConfig:
    id: str
    content_root: str
    exclusions: list[str] = field(default_factory=list)


@dataclass
class SteamConfig:
    app_id: str
    build_account: str
    default_branch: str
    depots: list[DepotConfig]

    @classmethod
    def from_dict(cls, raw: dict) -> "SteamConfig":
        depots = [
            DepotConfig(
                id=str(d.get("id", "")).strip(),
                content_root=str(d.get("content_root", "")).strip(),
                exclusions=list(d.get("exclusions") or []),
            )
            for d in (raw.get("depots") or [])
        ]
        return cls(
            app_id=str(raw.get("app_id", "")).strip(),
            build_account=str(raw.get("build_account", "")).strip(),
            default_branch=str(raw.get("default_branch") or "").strip(),
            depots=depots,
        )

    def to_dict(self) -> dict:
        return {
            "app_id": self.app_id,
            "build_account": self.build_account,
            "default_branch": self.default_branch,
            "depots": [
                {
                    "id": d.id,
                    "content_root": d.content_root,
                    **({"exclusions": d.exclusions} if d.exclusions else {}),
                }
                for d in self.depots
            ],
        }

    def validate(self) -> list[str]:
        """Return a list of human-readable problems. Empty list = ready to ship."""
        errs: list[str] = []
        if not self.app_id.isdigit():
            errs.append("Steam app_id must be a numeric string (e.g. '1234567').")
        if not self.build_account:
            errs.append("Set the build-account username before uploading.")
        if not self.depots:
            errs.append("Add at least one depot.")
        for i, d in enumerate(self.depots, start=1):
            if not d.id.isdigit():
                errs.append(f"Depot {i}: depot id must be numeric.")
            if not d.content_root:
                errs.append(f"Depot {i}: content_root is empty.")
        return errs


# Keys go into double-quoted Steamworks VDF. Escape backslashes + quotes.
def _vdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _vdf_quote_pair(key: str, value: str, indent: int = 1) -> str:
    pad = "\t" * indent
    return f'{pad}"{_vdf_escape(key)}"\t\t"{_vdf_escape(value)}"\n'


def _depot_vdf(depot: DepotConfig, project_root: Path) -> str:
    """Render a single depot manifest. content_root is project-relative."""
    out: list[str] = ['"DepotBuildConfig"\n{\n']
    out.append(_vdf_quote_pair("DepotID", depot.id))
    # ContentRoot is resolved absolutely so steamcmd doesn't depend on its cwd.
    cr_abs = (project_root / depot.content_root).resolve()
    out.append(_vdf_quote_pair("ContentRoot", str(cr_abs).replace("\\", "/")))
    out.append('\t"FileMapping"\n\t{\n')
    out.append(_vdf_quote_pair("LocalPath", "*", indent=2))
    out.append(_vdf_quote_pair("DepotPath", ".", indent=2))
    out.append(_vdf_quote_pair("recursive", "1", indent=2))
    out.append("\t}\n")
    for pattern in depot.exclusions:
        out.append(_vdf_quote_pair("FileExclusion", pattern))
    out.append("}\n")
    return "".join(out)


def _app_build_vdf(
    cfg: SteamConfig,
    depot_vdf_relpaths: dict[str, str],
    description: str,
    output_dir: Path,
    set_live: str = "",
    preview: bool = False,
) -> str:
    """Render the top-level app_build VDF.

    ``preview=True`` flips Steamworks' built-in dry-run flag — steamcmd will
    walk every depot and report what *would* be uploaded (file list, sizes)
    without actually shipping anything to Steam. This is what the Steam panel's
    "Preview" button uses to sanity-check a build before the real upload.
    """
    out: list[str] = ['"appbuild"\n{\n']
    out.append(_vdf_quote_pair("appid", cfg.app_id))
    out.append(_vdf_quote_pair("desc", description))
    out.append(_vdf_quote_pair("buildoutput", str(output_dir).replace("\\", "/")))
    out.append(_vdf_quote_pair("contentroot", ""))  # per-depot content roots
    out.append(_vdf_quote_pair("setlive", set_live))
    out.append(_vdf_quote_pair("preview", "1" if preview else "0"))
    out.append(_vdf_quote_pair("local", ""))
    out.append('\t"depots"\n\t{\n')
    for depot_id, rel in depot_vdf_relpaths.items():
        out.append(_vdf_quote_pair(depot_id, rel, indent=2))
    out.append("\t}\n}\n")
    return "".join(out)


@dataclass
class GeneratedVdfs:
    app_vdf: Path
    depot_vdfs: list[Path]
    output_dir: Path
    description: str


def generate_vdfs(
    project_root: str | Path,
    config: SteamConfig,
    description: str | None = None,
    set_live: str = "",
    preview: bool = False,
) -> GeneratedVdfs:
    """Write app_build.vdf + per-depot VDFs into ``<project>/.outlaw/steam/``.

    The output directory is created if missing. Existing VDFs are overwritten —
    they're 100% derived from the config so there's nothing to preserve. Pass
    ``preview=True`` to emit a dry-run VDF (``preview "1"``) — steamcmd will
    report what would be uploaded without actually shipping anything.

    Preview VDFs are written to a sibling file (``app_build.preview.vdf``) so
    the real upload can't accidentally reuse the dry-run file later.
    """
    project_root = Path(project_root)
    steam_dir = project_root / ".outlaw" / "steam"
    steam_dir.mkdir(parents=True, exist_ok=True)
    output_dir = steam_dir / ("preview-output" if preview else "output")
    output_dir.mkdir(parents=True, exist_ok=True)

    if description is None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        description = f"Outlaw CodeMaker {'preview' if preview else 'build'} {ts}"

    depot_paths: dict[str, str] = {}
    written_depot_files: list[Path] = []
    for depot in config.depots:
        depot_path = steam_dir / f"depot_{depot.id}.vdf"
        depot_path.write_text(_depot_vdf(depot, project_root), encoding="utf-8")
        written_depot_files.append(depot_path)
        depot_paths[depot.id] = depot_path.name  # app_build references siblings by name

    app_vdf_name = "app_build.preview.vdf" if preview else "app_build.vdf"
    app_vdf = steam_dir / app_vdf_name
    app_vdf.write_text(
        _app_build_vdf(config, depot_paths, description, output_dir,
                       set_live=set_live, preview=preview),
        encoding="utf-8",
    )
    return GeneratedVdfs(
        app_vdf=app_vdf,
        depot_vdfs=written_depot_files,
        output_dir=output_dir,
        description=description,
    )


# ---------------------------------------------------------------------------
# Upload + promote
# ---------------------------------------------------------------------------


_BUILD_ID_PATTERNS = [
    re.compile(r"Successfully finished AppID \d+ build \(BuildID (\d+)\)", re.IGNORECASE),
    re.compile(r"BuildID\s*[:=]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"Build ID\s*[:=]?\s*(\d+)", re.IGNORECASE),
]


def _parse_build_id(output: str) -> str | None:
    for pat in _BUILD_ID_PATTERNS:
        m = pat.search(output)
        if m:
            return m.group(1)
    return None


@dataclass
class UploadResult:
    ok: bool
    build_id: str | None
    log: str
    error: str = ""


def run_steamcmd_stream(
    steamcmd_path: str,
    args: list[str],
    on_line=None,
    timeout: int = 60 * 60 * 4,  # 4-hour ceiling — uploads can take a while
    interruption_check=None,
) -> tuple[int, str]:
    """Run steamcmd, streaming each stdout line through ``on_line``.

    Returns (exit_code, full_output). ``interruption_check`` is an optional
    callable returning True when the worker should kill the subprocess.
    """
    proc = subprocess.Popen(
        [steamcmd_path, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered
    )
    collected: list[str] = []
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            collected.append(line)
            if on_line:
                try:
                    on_line(line)
                except Exception:  # noqa: BLE001
                    logger.exception("steamcmd line callback failed")
            if interruption_check and interruption_check():
                proc.terminate()
                break
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        collected.append(f"[outlaw] killed after {timeout}s timeout")
    return proc.returncode or 0, "\n".join(collected)


class SteamPublisher:
    """Thin orchestrator. UI-friendly helpers; the QThread workers below use
    these in the background."""

    def __init__(self, steamcmd_path: str, project_root: Path):
        self.steamcmd_path = steamcmd_path
        self.project_root = Path(project_root)

    def upload(
        self,
        app_vdf: Path,
        username: str,
        on_line=None,
        interruption_check=None,
    ) -> UploadResult:
        if not Path(app_vdf).exists():
            return UploadResult(False, None, "", f"VDF not found: {app_vdf}")
        args = [
            "+login", username,
            "+run_app_build", str(app_vdf).replace("\\", "/"),
            "+quit",
        ]
        code, log = run_steamcmd_stream(
            self.steamcmd_path, args, on_line=on_line,
            interruption_check=interruption_check,
        )
        if code != 0:
            return UploadResult(False, _parse_build_id(log), log,
                                f"steamcmd exited with code {code}")
        build_id = _parse_build_id(log)
        return UploadResult(True, build_id, log, "" if build_id else "(no BuildID found in output)")

    def preview(
        self,
        app_vdf: Path,
        username: str,
        on_line=None,
        interruption_check=None,
    ) -> UploadResult:
        """Run a Steamworks dry-run. Same command as :meth:`upload` but the VDF
        has ``preview "1"`` set — steamcmd reports what *would* upload (file
        list, sizes, manifest delta) without actually shipping a build.
        The returned ``build_id`` is always None for previews; ``ok`` reflects
        whether steamcmd exited cleanly.
        """
        if not Path(app_vdf).exists():
            return UploadResult(False, None, "", f"Preview VDF not found: {app_vdf}")
        args = [
            "+login", username,
            "+run_app_build", str(app_vdf).replace("\\", "/"),
            "+quit",
        ]
        code, log = run_steamcmd_stream(
            self.steamcmd_path, args, on_line=on_line,
            interruption_check=interruption_check,
        )
        ok = code == 0
        return UploadResult(
            ok, None, log,
            "" if ok else f"steamcmd exited with code {code} during preview.",
        )

    def promote(
        self,
        app_id: str,
        build_id: str,
        branch: str,
        username: str,
        on_line=None,
    ) -> UploadResult:
        """Set a branch's live build via ``+app_build_set_live``."""
        if not all([app_id, build_id, branch, username]):
            return UploadResult(False, None, "", "app_id, build_id, branch, and username are required.")
        args = [
            "+login", username,
            "+app_build_set_live", app_id, build_id, branch,
            "+quit",
        ]
        code, log = run_steamcmd_stream(self.steamcmd_path, args, on_line=on_line)
        ok = code == 0 and not re.search(r"FAIL", log, re.IGNORECASE)
        return UploadResult(ok, build_id, log,
                            "" if ok else f"Promote failed (exit {code}). See log.")


# ---------------------------------------------------------------------------
# Qt workers — run upload/promote off the UI thread
# ---------------------------------------------------------------------------


class SteamUploadWorker(QThread):
    """QThread that runs an upload and streams output lines to the UI."""

    line = pyqtSignal(str)
    finished_with_result = pyqtSignal(object)  # UploadResult

    def __init__(
        self,
        publisher: SteamPublisher,
        app_vdf: Path,
        username: str,
        parent=None,
    ):
        super().__init__(parent)
        self.publisher = publisher
        self.app_vdf = app_vdf
        self.username = username

    def run(self) -> None:
        result = self.publisher.upload(
            self.app_vdf,
            self.username,
            on_line=lambda s: self.line.emit(s),
            interruption_check=self.isInterruptionRequested,
        )
        self.finished_with_result.emit(result)


class SteamPromoteWorker(QThread):
    line = pyqtSignal(str)
    finished_with_result = pyqtSignal(object)  # UploadResult

    def __init__(
        self,
        publisher: SteamPublisher,
        app_id: str,
        build_id: str,
        branch: str,
        username: str,
        parent=None,
    ):
        super().__init__(parent)
        self.publisher = publisher
        self.app_id = app_id
        self.build_id = build_id
        self.branch = branch
        self.username = username

    def run(self) -> None:
        result = self.publisher.promote(
            self.app_id, self.build_id, self.branch, self.username,
            on_line=lambda s: self.line.emit(s),
        )
        self.finished_with_result.emit(result)


class SteamPreviewWorker(QThread):
    """Dry-run twin of :class:`SteamUploadWorker`. Same shape, different
    semantics — no build is shipped, the output is just steamcmd's report of
    what would happen."""

    line = pyqtSignal(str)
    finished_with_result = pyqtSignal(object)  # UploadResult

    def __init__(
        self,
        publisher: SteamPublisher,
        app_vdf: Path,
        username: str,
        parent=None,
    ):
        super().__init__(parent)
        self.publisher = publisher
        self.app_vdf = app_vdf
        self.username = username

    def run(self) -> None:
        result = self.publisher.preview(
            self.app_vdf,
            self.username,
            on_line=lambda s: self.line.emit(s),
            interruption_check=self.isInterruptionRequested,
        )
        self.finished_with_result.emit(result)


# ---------------------------------------------------------------------------
# Preview output summarizer (parses the steamcmd dry-run log)
# ---------------------------------------------------------------------------


@dataclass
class PreviewSummary:
    """Human-readable rollup of a preview run for the confirmation dialog."""
    file_count: int
    total_bytes: int
    depots_seen: list[str]
    sample_paths: list[str]  # up to ~20 representative file paths
    raw_tail: str            # last ~1500 chars of steamcmd output for debug

    @property
    def total_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)


# steamcmd's preview output isn't a perfectly stable format, but these regexes
# match the common lines emitted by recent ContentBuilder versions.
_PREVIEW_FILE_PATTERNS = [
    re.compile(r"^File '([^']+)'.*?\(\s*(\d+)\s*bytes?\s*\)", re.IGNORECASE),
    re.compile(r"^\s*Adding\s+(\S+)\s+\((\d+)\s*bytes?\)", re.IGNORECASE),
    re.compile(r"^\s*([^\s].+?)\s+(\d+)\s*bytes?\s*$", re.IGNORECASE),
]
_PREVIEW_DEPOT_RE = re.compile(r"Depot\s+(\d+)", re.IGNORECASE)


def summarize_preview(log: str) -> PreviewSummary:
    """Best-effort parse of a steamcmd preview run. Always returns a summary —
    if the regexes catch nothing we still surface the raw tail so the user can
    eyeball it instead of getting a blank dialog.
    """
    file_count = 0
    total_bytes = 0
    depots: set[str] = set()
    samples: list[str] = []
    for line in (log or "").splitlines():
        m_depot = _PREVIEW_DEPOT_RE.search(line)
        if m_depot:
            depots.add(m_depot.group(1))
        for pat in _PREVIEW_FILE_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            path, size = m.group(1), m.group(2)
            try:
                n = int(size)
            except ValueError:
                continue
            # Filter out cases where the path is obviously a number / noise.
            if len(path) < 2 or path.isdigit():
                continue
            file_count += 1
            total_bytes += n
            if len(samples) < 20:
                samples.append(path)
            break  # match only one pattern per line
    return PreviewSummary(
        file_count=file_count,
        total_bytes=total_bytes,
        depots_seen=sorted(depots),
        sample_paths=samples,
        raw_tail=(log or "")[-1500:],
    )
