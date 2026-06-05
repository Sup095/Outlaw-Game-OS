"""Steam panel — the "Ship to Steam" UI inside Outlaw CodeMaker.

Read/writes Steam config in ``<project>/.outlaw/project.json`` via the helpers
in ``vision.godot_project``. Runs uploads + branch promotions on background
``QThread`` workers from ``core.steam_publish`` so the UI stays responsive.

The panel never stores Steam passwords. The user runs
``steamcmd +login <user>`` once interactively (Steam Guard / 2FA) and steamcmd
caches the session for ~30 days; we just call it with ``+login <user>`` and
trust the cache. If the cache expires, the status row says so and the build
button is disabled with a clear error.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import aur_install
from core.steam_publish import (
    DepotConfig,
    GeneratedVdfs,
    PreviewSummary,
    SteamConfig,
    SteamPreviewWorker,
    SteamPromoteWorker,
    SteamPublisher,
    SteamUploadWorker,
    check_login,
    check_steamcmd,
    find_steamcmd,
    generate_vdfs,
    summarize_preview,
)
from vision.godot_project import read_outlaw_metadata, write_outlaw_metadata

from .styles import COLORS, dot


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AUR install dialog (used for on-demand `steamcmd` install via the privileged
# /usr/local/bin/outlaw-install-aur helper that ships in Outlaw OS).
# ---------------------------------------------------------------------------


class _AurInstallWorker(QThread):
    """Runs :func:`core.aur_install.install_package` off the GUI thread.

    Each line of helper output is emitted via :attr:`line` so the dialog can
    append it live. When the install finishes (success or failure), the
    final :class:`AurInstallResult` is emitted via :attr:`finished_result`.
    The dialog connects to both and closes itself on completion.
    """

    line = pyqtSignal(str)
    finished_result = pyqtSignal(object)   # AurInstallResult

    def __init__(self, package: str, parent=None):
        super().__init__(parent)
        self._package = package

    def run(self) -> None:  # noqa: D401 — QThread API
        result = aur_install.install_package(
            self._package,
            on_line=lambda s: self.line.emit(s),
        )
        self.finished_result.emit(result)


class _AurInstallDialog(QDialog):
    """Modal "Install steamcmd from AUR" progress dialog.

    Shows a live-streaming log of the helper's output, an indeterminate
    progress bar while running, and a Close button that's disabled until
    the install finishes. The caller reads :attr:`result` after the dialog
    returns to decide how to react.

    Usage::

        dlg = _AurInstallDialog("steamcmd", parent=self)
        dlg.exec()  # blocks until install finishes (or user cancels by closing)
        if dlg.result_obj and dlg.result_obj.ok:
            ...
    """

    def __init__(self, package: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Installing {package} from AUR")
        self.setModal(True)
        self.resize(640, 360)
        self.result_obj: aur_install.AurInstallResult | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        header = QLabel(
            f"Installing <b>{html.escape(package)}</b> via the AUR helper.<br>"
            "<span style='color:#888;'>This usually takes 20–60 seconds and "
            "needs a working network connection. You can leave this open while "
            "you keep using CodeMaker — it'll just be unresponsive while the "
            "build runs.</span>"
        )
        header.setWordWrap(True)
        header.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(header)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setMaximumHeight(8)
        outer.addWidget(self._progress)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setObjectName("TerminalLog")
        outer.addWidget(self._log, 1)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # Close stays disabled until the worker finishes. The user *can*
        # force-close via the window's X button — Qt will tear the worker
        # down cleanly because we don't hold native resources from inside it.
        self._close_btn = self._buttons.button(QDialogButtonBox.StandardButton.Close)
        self._close_btn.setEnabled(False)
        self._buttons.rejected.connect(self.accept)
        outer.addWidget(self._buttons)

        self._worker = _AurInstallWorker(package, parent=self)
        self._worker.line.connect(self._append_line)
        self._worker.finished_result.connect(self._on_finished)
        self._worker.start()

    def _append_line(self, line: str) -> None:
        self._log.appendPlainText(line)

    def _on_finished(self, result: aur_install.AurInstallResult) -> None:
        self.result_obj = result
        self._progress.setRange(0, 1)
        self._progress.setValue(1)

        # Trailer line for clarity, since helper output ends mid-sentence.
        if result.ok:
            self._log.appendPlainText("")
            self._log.appendPlainText(f"✓ {result.note}")
        else:
            self._log.appendPlainText("")
            self._log.appendPlainText(f"✗ {result.note}")

        self._close_btn.setEnabled(True)
        self._close_btn.setFocus()


# ---------------------------------------------------------------------------
# Depot row widget
# ---------------------------------------------------------------------------


class _DepotRow(QWidget):
    """One row in the depot editor: depot id, content root, exclusions."""

    removed = pyqtSignal(QWidget)

    def __init__(self, depot: DepotConfig | None = None):
        super().__init__()
        depot = depot or DepotConfig(id="", content_root="", exclusions=[])

        self.id_edit = QLineEdit(depot.id)
        self.id_edit.setPlaceholderText("Depot ID (numeric)")
        self.id_edit.setMaximumWidth(180)

        self.root_edit = QLineEdit(depot.content_root)
        self.root_edit.setPlaceholderText("Content root (project-relative, e.g. build/windows)")

        self.excl_edit = QLineEdit(", ".join(depot.exclusions))
        self.excl_edit.setPlaceholderText("Exclusions, comma-separated (e.g. *.pdb, *.log)")

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(34)
        remove_btn.setToolTip("Remove depot")
        remove_btn.clicked.connect(lambda: self.removed.emit(self))

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)
        row.addWidget(self.id_edit, 0)
        row.addWidget(self.root_edit, 1)
        row.addWidget(self.excl_edit, 1)
        row.addWidget(remove_btn, 0)

    def to_config(self) -> DepotConfig:
        exclusions = [x.strip() for x in self.excl_edit.text().split(",") if x.strip()]
        return DepotConfig(
            id=self.id_edit.text().strip(),
            content_root=self.root_edit.text().strip(),
            exclusions=exclusions,
        )


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------


class SteamPanel(QWidget):
    """Project-scoped Steam publishing panel. Pass a workspace_root (project
    folder containing project.godot + .outlaw/). Call set_workspace() when
    the orchestrator switches projects."""

    log_message = pyqtSignal(str, str)  # (level, message) — forwarded to TerminalLog

    def __init__(self, workspace_root: str = "", parent=None):
        super().__init__(parent)
        self.workspace_root = workspace_root
        self._upload_worker: SteamUploadWorker | None = None
        self._promote_worker: SteamPromoteWorker | None = None
        self._preview_worker: SteamPreviewWorker | None = None
        self._last_build_id: str | None = None
        self._last_preview_summary: PreviewSummary | None = None
        self._steamcmd_path: str | None = None

        self._build_ui()
        self._refresh_status()
        self.load_config()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Header
        header = QFrame()
        header.setObjectName("PanelHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        title = QLabel("STEAM PUBLISH")
        title.setObjectName("PanelTitle")
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setFixedWidth(34)
        self.refresh_btn.setToolTip("Re-check steamcmd + login")
        self.refresh_btn.clicked.connect(self._refresh_status)
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(self.refresh_btn)

        # Status row
        self.status_label = QLabel("")
        self.status_label.setTextFormat(Qt.TextFormat.RichText)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            f"background:{COLORS['panel']};border:1px solid {COLORS['border']};"
            f"border-radius:8px;padding:8px 12px;"
        )

        # Config form
        self.app_id_edit = QLineEdit()
        self.app_id_edit.setPlaceholderText("e.g. 1234567")
        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("Steam build-account username")
        self.branch_edit = QLineEdit()
        self.branch_edit.setPlaceholderText("default (leave 'default' unless you have a beta branch)")
        self.steamcmd_edit = QLineEdit()
        self.steamcmd_edit.setPlaceholderText("Auto-detected — override only if needed")
        self.steamcmd_edit.editingFinished.connect(self._refresh_status)

        # Install-from-AUR shortcut. Visible only when steamcmd is missing
        # AND the privileged outlaw-install-aur helper is present on this
        # host (i.e. we're running on Outlaw OS). Wired in _refresh_status.
        self.install_steamcmd_btn = QPushButton("⤓  Install steamcmd from AUR")
        self.install_steamcmd_btn.setToolTip(
            "steamcmd lives in the Arch User Repository (AUR), not the official "
            "repos, so Outlaw OS doesn't bundle it. This button asks for your "
            "password (polkit), then downloads + builds it (~30 sec, ~2 MB)."
        )
        self.install_steamcmd_btn.clicked.connect(self._on_install_steamcmd_clicked)
        self.install_steamcmd_btn.setVisible(False)

        cfg_form = QFormLayout()
        cfg_form.setSpacing(8)
        cfg_form.addRow("App ID", self.app_id_edit)
        cfg_form.addRow("Build account", self.account_edit)
        cfg_form.addRow("Default branch", self.branch_edit)
        cfg_form.addRow("steamcmd path", self.steamcmd_edit)

        cfg_card = QFrame()
        cfg_card.setStyleSheet(
            f"QFrame{{background:{COLORS['panel']};border:1px solid {COLORS['border']};"
            f"border-radius:8px;}}"
        )
        cfg_card_layout = QVBoxLayout(cfg_card)
        cfg_card_layout.setContentsMargins(12, 10, 12, 10)
        cfg_card_layout.addLayout(cfg_form)
        # Install button sits flush-left below the form. Indented to align
        # with the QLineEdit column, not the label column.
        install_row = QHBoxLayout()
        install_row.addStretch(1)
        install_row.addWidget(self.install_steamcmd_btn, 0)
        cfg_card_layout.addLayout(install_row)

        # Depots editor
        self.depots_container = QWidget()
        self.depots_layout = QVBoxLayout(self.depots_container)
        self.depots_layout.setContentsMargins(0, 0, 0, 0)
        self.depots_layout.setSpacing(4)

        depots_card = QFrame()
        depots_card.setStyleSheet(
            f"QFrame{{background:{COLORS['panel']};border:1px solid {COLORS['border']};"
            f"border-radius:8px;}}"
        )
        depots_layout = QVBoxLayout(depots_card)
        depots_layout.setContentsMargins(12, 10, 12, 10)
        depots_layout.setSpacing(6)
        depots_layout.addWidget(self._small_header("Depots"))
        depots_layout.addWidget(self.depots_container)
        add_btn = QPushButton("＋ Add depot")
        add_btn.clicked.connect(lambda: self._add_depot_row(None))
        depots_layout.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)

        # Save / verify row
        self.save_btn = QPushButton("Save Steam config")
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self.save_config)
        self.verify_btn = QPushButton("Verify steamcmd + login")
        self.verify_btn.clicked.connect(self._refresh_status)

        save_row = QHBoxLayout()
        save_row.addWidget(self.verify_btn)
        save_row.addStretch(1)
        save_row.addWidget(self.save_btn)

        # Build & Upload
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Optional build description (auto if blank)")
        self.preview_btn = QPushButton("👁  Preview (dry-run)")
        self.preview_btn.setToolTip(
            "Run a Steamworks dry-run via steamcmd — report what would be uploaded "
            "(file list, total size) without actually shipping anything. Always do "
            "this before a real upload."
        )
        self.preview_btn.clicked.connect(self._on_preview_clicked)
        self.upload_btn = QPushButton("⏏  Build & Upload to Steam")
        self.upload_btn.setObjectName("PrimaryButton")
        self.upload_btn.clicked.connect(self._on_upload_clicked)
        self.cancel_btn = QPushButton("■ Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # busy spinner when active
        self.progress.setVisible(False)
        self.progress.setMaximumHeight(8)

        upload_card = QFrame()
        upload_card.setStyleSheet(
            f"QFrame{{background:{COLORS['panel']};border:1px solid {COLORS['border']};"
            f"border-radius:8px;}}"
        )
        upload_layout = QVBoxLayout(upload_card)
        upload_layout.setContentsMargins(12, 10, 12, 10)
        upload_layout.setSpacing(8)
        upload_layout.addWidget(self._small_header("Build & Upload"))
        desc_row = QFormLayout()
        desc_row.addRow("Description", self.desc_edit)
        upload_layout.addLayout(desc_row)
        action_row = QHBoxLayout()
        action_row.addWidget(self.preview_btn, 0)
        action_row.addWidget(self.upload_btn, 1)
        action_row.addWidget(self.cancel_btn, 0)
        upload_layout.addLayout(action_row)
        upload_layout.addWidget(self.progress)

        # Promote
        self.promote_build_edit = QLineEdit()
        self.promote_build_edit.setPlaceholderText("Build ID (auto-filled after a successful upload)")
        self.promote_branch_combo = QComboBox()
        self.promote_branch_combo.setEditable(True)
        self.promote_branch_combo.addItems(["default", "beta", "preview"])
        self.promote_btn = QPushButton("🚀  Promote to branch")
        self.promote_btn.clicked.connect(self._on_promote_clicked)

        promote_card = QFrame()
        promote_card.setStyleSheet(
            f"QFrame{{background:{COLORS['panel']};border:1px solid {COLORS['border']};"
            f"border-radius:8px;}}"
        )
        promote_layout = QVBoxLayout(promote_card)
        promote_layout.setContentsMargins(12, 10, 12, 10)
        promote_layout.setSpacing(8)
        promote_layout.addWidget(self._small_header("Promote to branch"))
        promote_form = QFormLayout()
        promote_form.addRow("Build ID", self.promote_build_edit)
        promote_form.addRow("Branch", self.promote_branch_combo)
        promote_layout.addLayout(promote_form)
        promote_layout.addWidget(self.promote_btn, 0, Qt.AlignmentFlag.AlignLeft)

        # Log view
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("TerminalLog")
        self.log_view.setMinimumHeight(160)
        self.log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        log_card = QFrame()
        log_card.setStyleSheet(
            f"QFrame{{background:{COLORS['panel']};border:1px solid {COLORS['border']};"
            f"border-radius:8px;}}"
        )
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(12, 10, 12, 10)
        log_layout.setSpacing(6)
        log_layout.addWidget(self._small_header("steamcmd output"))
        log_layout.addWidget(self.log_view, 1)

        # Outer scroll
        inner = QWidget()
        inner_v = QVBoxLayout(inner)
        inner_v.setContentsMargins(10, 10, 10, 10)
        inner_v.setSpacing(10)
        inner_v.addWidget(self.status_label)
        inner_v.addWidget(cfg_card)
        inner_v.addWidget(depots_card)
        inner_v.addLayout(save_row)
        inner_v.addWidget(upload_card)
        inner_v.addWidget(promote_card)
        inner_v.addWidget(log_card, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        outer.addWidget(scroll, 1)

    def _small_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{COLORS['accent']};font-weight:700;letter-spacing:1px;"
        )
        return lbl

    # ------------------------------------------------------------------
    # Workspace + persistence
    # ------------------------------------------------------------------

    def set_workspace(self, workspace_root: str) -> None:
        """Re-point at a different project. Clears the form and re-loads."""
        self.workspace_root = workspace_root
        self._last_build_id = None
        # A preview from a previous project doesn't tell us anything about this
        # one — drop it so the "did you preview?" gate kicks in for the new project.
        self._last_preview_summary = None
        self.promote_build_edit.setText("")
        self.log_view.clear()
        self.load_config()
        self._refresh_status()

    def load_config(self) -> None:
        """Read .outlaw/project.json and populate the form."""
        self._clear_depots()
        if not self.workspace_root:
            self._populate_form(SteamConfig(app_id="", build_account="", default_branch="default", depots=[]))
            return
        meta = read_outlaw_metadata(self.workspace_root) or {}
        steam_raw = meta.get("steam") or {}
        cfg = SteamConfig.from_dict(steam_raw)
        if not cfg.default_branch:
            cfg.default_branch = "default"
        self._populate_form(cfg)

    def save_config(self) -> None:
        """Write the form values back to the project sidecar."""
        if not self.workspace_root:
            QMessageBox.warning(self, "No project open", "Open a project first.")
            return
        cfg = self._read_form()
        errors = cfg.validate()
        # Save even if invalid — user might be partway through filling it in.
        # Persist the steamcmd_path alongside the SteamConfig dict so the
        # override survives across opens of the project.
        payload = cfg.to_dict()
        steamcmd_override = self.steamcmd_edit.text().strip()
        if steamcmd_override:
            payload["steamcmd_path"] = steamcmd_override
        try:
            write_outlaw_metadata(self.workspace_root, {"steam": payload})
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", f"Couldn't write .outlaw/project.json:\n\n{exc}")
            return
        if errors:
            self._append_log("warn", "Saved with warnings:\n  - " + "\n  - ".join(errors))
        else:
            self._append_log("ok", "Steam config saved.")
        self._refresh_status()

    # ------------------------------------------------------------------
    # Form helpers
    # ------------------------------------------------------------------

    def _populate_form(self, cfg: SteamConfig) -> None:
        self.app_id_edit.setText(cfg.app_id)
        self.account_edit.setText(cfg.build_account)
        self.branch_edit.setText(cfg.default_branch or "default")
        # steamcmd override is global per-project — read from same sidecar if present.
        meta = read_outlaw_metadata(self.workspace_root) or {} if self.workspace_root else {}
        self.steamcmd_edit.setText((meta.get("steam") or {}).get("steamcmd_path", ""))
        for depot in cfg.depots:
            self._add_depot_row(depot)

    def _read_form(self) -> SteamConfig:
        depots: list[DepotConfig] = []
        for i in range(self.depots_layout.count()):
            row = self.depots_layout.itemAt(i).widget()
            if isinstance(row, _DepotRow):
                depots.append(row.to_config())
        return SteamConfig(
            app_id=self.app_id_edit.text().strip(),
            build_account=self.account_edit.text().strip(),
            default_branch=self.branch_edit.text().strip() or "default",
            depots=depots,
        )

    def _add_depot_row(self, depot: DepotConfig | None) -> None:
        row = _DepotRow(depot)
        row.removed.connect(self._remove_depot_row)
        self.depots_layout.addWidget(row)

    def _remove_depot_row(self, row: QWidget) -> None:
        row.setParent(None)
        row.deleteLater()

    def _clear_depots(self) -> None:
        while self.depots_layout.count():
            item = self.depots_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        override = self.steamcmd_edit.text().strip() or None
        status = check_steamcmd(override)
        self._steamcmd_path = status.path

        bits: list[str] = []
        if status.available and status.path:
            bits.append(
                f'{dot("ok")} steamcmd <span style="color:{COLORS["muted"]};">'
                f'{html.escape(status.path)}</span>'
            )
        else:
            bits.append(
                f'{dot("err")} steamcmd <span style="color:{COLORS["muted"]};">'
                f'{html.escape(status.note or "not found")}</span>'
            )

        # Surface the AUR install shortcut only when both conditions hold:
        # (1) we don't currently have steamcmd, AND (2) the privileged helper
        # is present on this host. On non-Outlaw-OS Linux (vanilla Arch, Mac
        # dev workstation, etc.) the button stays hidden and the user sees
        # the existing "install manually" note.
        self.install_steamcmd_btn.setVisible(
            not status.available and aur_install.helper_available()
        )

        # Config validity
        cfg = self._read_form()
        errors = cfg.validate()
        if errors:
            bits.append(
                f'{dot("warn")} config <span style="color:{COLORS["muted"]};">'
                f'{len(errors)} issue(s): {html.escape(errors[0])}</span>'
            )
        else:
            bits.append(
                f'{dot("ok")} config <span style="color:{COLORS["muted"]};">'
                f'app {html.escape(cfg.app_id)} · {len(cfg.depots)} depot(s)</span>'
            )

        # Login (only meaningful when we have steamcmd and a username)
        if status.available and status.path and cfg.build_account:
            # Run synchronously but with a 12s timeout — it's a single HTTP-ish handshake.
            login = check_login(status.path, cfg.build_account, timeout=12)
            d = "ok" if login.logged_in else "warn"
            bits.append(
                f'{dot(d)} login <span style="color:{COLORS["muted"]};">'
                f'{html.escape(login.message)}</span>'
            )
            self._login_ok = login.logged_in
        else:
            bits.append(
                f'{dot("muted")} login <span style="color:{COLORS["muted"]};">'
                f'set build account + steamcmd to check</span>'
            )
            self._login_ok = False

        self.status_label.setText("<br>".join(bits))

        # Gate the upload button on hard prerequisites.
        ready = bool(status.available and status.path and not errors)
        self.upload_btn.setEnabled(ready and self._upload_worker is None)
        self.promote_btn.setEnabled(ready and self._promote_worker is None)

    # ------------------------------------------------------------------
    # AUR install (on-demand steamcmd)
    # ------------------------------------------------------------------

    def _on_install_steamcmd_clicked(self) -> None:
        """Run the privileged AUR install for steamcmd, then re-refresh.

        The install runs in a worker thread under :class:`_AurInstallDialog`
        so the GUI stays responsive. If the user authenticates and the
        build succeeds, the resulting binary lands on $PATH and the next
        :meth:`_refresh_status` picks it up automatically.
        """
        # Belt-and-braces: re-check availability right before the click,
        # in case the user installed it manually since the last refresh.
        if aur_install.is_already_installed("steamcmd"):
            self._refresh_status()
            return

        # Short confirmation to set expectations. This is the moment a
        # password prompt is about to appear, so warn the user.
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Install steamcmd from AUR")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText(
            "Install <b>steamcmd</b> from the Arch User Repository?"
        )
        confirm.setInformativeText(
            "You'll be asked for your password (via polkit). The build "
            "takes roughly 30 seconds and downloads about 2 MB.<br><br>"
            "steamcmd is the official Steamworks command-line tool used "
            "to upload Godot builds to your Steam app."
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.Ok)
        if confirm.exec() != QMessageBox.StandardButton.Ok:
            return

        dlg = _AurInstallDialog("steamcmd", parent=self)
        dlg.exec()

        result = dlg.result_obj
        if result is None:
            # Dialog closed without the worker finishing — shouldn't happen
            # in practice (close button stays disabled until done) but be
            # defensive in case the user force-quit via window manager.
            return

        if result.ok:
            self.log_message.emit("info", f"steamcmd installed: {result.note}")
        else:
            self.log_message.emit("error", f"steamcmd install failed: {result.note}")
            # If pkexec/polkit weren't available we can't retry — show the
            # manual-install instructions from the result note.
            if result.needs_manual_install:
                QMessageBox.information(
                    self, "Manual install required", result.note
                )

        self._refresh_status()

    # ------------------------------------------------------------------
    # Upload flow
    # ------------------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        if self._preview_worker or self._upload_worker:
            return
        if not self.workspace_root:
            QMessageBox.warning(self, "No project open", "Open a project first.")
            return
        if not self._steamcmd_path:
            QMessageBox.warning(
                self,
                "steamcmd missing",
                "steamcmd isn't installed yet. Use the 'Install steamcmd from "
                "AUR' button above (if you're on Outlaw OS) or set its path "
                "manually in the Config form.",
            )
            return
        cfg = self._read_form()
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Fix config first", "\n".join(errors))
            return
        # Persist the form so the preview VDFs match what an actual upload would see.
        try:
            write_outlaw_metadata(self.workspace_root, {"steam": cfg.to_dict()})
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", f"Couldn't write project metadata:\n\n{exc}")
            return
        try:
            generated: GeneratedVdfs = generate_vdfs(
                self.workspace_root,
                cfg,
                description=self.desc_edit.text().strip() or None,
                preview=True,
            )
        except OSError as exc:
            QMessageBox.critical(self, "Preview VDF failed", str(exc))
            return
        self._append_log("info", f"Preview VDF: {generated.app_vdf} (preview=1, no upload).")

        publisher = SteamPublisher(self._steamcmd_path, Path(self.workspace_root))
        worker = SteamPreviewWorker(publisher, generated.app_vdf, cfg.build_account)
        worker.line.connect(self._on_worker_line)
        worker.finished_with_result.connect(self._on_preview_finished)
        worker.finished.connect(worker.deleteLater)
        self._preview_worker = worker
        self._set_busy(True)
        self._append_log("info", "▶ Steam preview starting…")
        worker.start()

    def _on_preview_finished(self, result) -> None:
        self._preview_worker = None
        self._set_busy(False)
        if not result.ok:
            self._append_log("error", f"Preview failed: {result.error}")
            QMessageBox.warning(self, "Preview failed", result.error or "Unknown error.")
            return
        summary = summarize_preview(result.log)
        self._last_preview_summary = summary
        self._append_log(
            "ok",
            f"Preview OK — would upload {summary.file_count} file(s), "
            f"{summary.total_mb:.1f} MB total across {len(summary.depots_seen) or '?'} depot(s).",
        )
        self._show_preview_dialog(summary)

    def _show_preview_dialog(self, summary: PreviewSummary) -> None:
        """Modal results window — what would have been uploaded."""
        from .styles import COLORS
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Steam preview — would upload")
        dlg.setIcon(QMessageBox.Icon.Information)
        depots = ", ".join(summary.depots_seen) if summary.depots_seen else "(none parsed)"
        sample = "\n".join(f"  {p}" for p in summary.sample_paths[:15]) or "(no file paths parsed — see log)"
        more = "" if len(summary.sample_paths) <= 15 else f"\n  …+ {summary.file_count - 15} more"
        body = (
            f"This is a dry-run — nothing has been uploaded.\n\n"
            f"Files: {summary.file_count}\n"
            f"Total size: {summary.total_mb:.1f} MB\n"
            f"Depots touched: {depots}\n\n"
            f"Sample paths:\n{sample}{more}\n\n"
            "If this looks right, click 'Build & Upload to Steam' to ship the real build."
        )
        dlg.setText(body)
        # Show the steamcmd tail in the details pane so the user can dig in.
        dlg.setDetailedText(summary.raw_tail)
        dlg.setStyleSheet(f"QLabel{{font-family:Consolas,monospace;min-width:560px;color:{COLORS['text']};}}")
        dlg.exec()

    def _on_upload_clicked(self) -> None:
        if self._upload_worker:
            return
        if not self.workspace_root:
            QMessageBox.warning(self, "No project open", "Open a project first.")
            return
        if not self._steamcmd_path:
            QMessageBox.warning(
                self,
                "steamcmd missing",
                "steamcmd isn't installed yet. Use the 'Install steamcmd from "
                "AUR' button above (if you're on Outlaw OS) or set its path "
                "manually in the Config form.",
            )
            return

        cfg = self._read_form()
        errors = cfg.validate()
        if errors:
            QMessageBox.warning(self, "Fix config first", "\n".join(errors))
            return

        # If the user hasn't done a preview in this session, ask twice. Previews
        # are free (no upload) — they catch 99% of "wrong content root" mistakes
        # before they hit live Steamworks.
        if self._last_preview_summary is None:
            first = QMessageBox.warning(
                self,
                "Skip the preview?",
                "You haven't run a Preview (dry-run) yet for this session.\n\n"
                "Preview is free and shows exactly what would be uploaded — strongly "
                "recommended before shipping to live Steamworks. Run it first?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if first == QMessageBox.StandardButton.Yes:
                self._on_preview_clicked()
                return
            # User chose to skip — make sure they really mean it.

        confirm_text = (
            f"Generate VDFs and run steamcmd to upload a build of app "
            f"{cfg.app_id} with {len(cfg.depots)} depot(s)?\n\n"
            "This ships to the real Steamworks endpoint. Make sure your depots' "
            "content roots have the files you want to ship."
        )
        if self._last_preview_summary:
            confirm_text += (
                f"\n\nLast preview reported {self._last_preview_summary.file_count} file(s), "
                f"{self._last_preview_summary.total_mb:.1f} MB."
            )
        confirm = QMessageBox.question(
            self, "Upload to Steam?", confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # Save the current form so we don't ship stale VDFs.
        try:
            write_outlaw_metadata(self.workspace_root, {"steam": cfg.to_dict()})
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", f"Couldn't write project metadata:\n\n{exc}")
            return

        # Generate VDFs
        try:
            generated: GeneratedVdfs = generate_vdfs(
                self.workspace_root,
                cfg,
                description=self.desc_edit.text().strip() or None,
            )
        except OSError as exc:
            QMessageBox.critical(self, "VDF generation failed", str(exc))
            return
        self._append_log("info", f"Generated {generated.app_vdf} and {len(generated.depot_vdfs)} depot VDFs.")
        self._append_log("info", f"Description: {generated.description}")

        # Spawn worker
        publisher = SteamPublisher(self._steamcmd_path, Path(self.workspace_root))
        worker = SteamUploadWorker(publisher, generated.app_vdf, cfg.build_account)
        worker.line.connect(self._on_worker_line)
        worker.finished_with_result.connect(self._on_upload_finished)
        worker.finished.connect(worker.deleteLater)
        self._upload_worker = worker
        self._set_busy(True)
        self._append_log("info", "▶ steamcmd starting…")
        worker.start()

    def _on_upload_finished(self, result) -> None:
        self._upload_worker = None
        self._set_busy(False)
        if result.ok:
            self._append_log("ok", f"Upload OK. BuildID = {result.build_id or '?'}.")
            if result.build_id:
                self._last_build_id = result.build_id
                self.promote_build_edit.setText(result.build_id)
        else:
            self._append_log("error", f"Upload failed: {result.error}")
        self._refresh_status()

    def _on_cancel_clicked(self) -> None:
        # Cancel applies to whichever worker is live — upload or preview.
        for worker in (self._upload_worker, self._preview_worker):
            if worker and worker.isRunning():
                worker.requestInterruption()
                self._append_log("warn", "Stopping steamcmd at next checkpoint…")
                return

    def _on_worker_line(self, line: str) -> None:
        self._append_raw(line)

    # ------------------------------------------------------------------
    # Promote flow
    # ------------------------------------------------------------------

    def _on_promote_clicked(self) -> None:
        if self._promote_worker:
            return
        if not self._steamcmd_path:
            QMessageBox.warning(
                self,
                "steamcmd missing",
                "steamcmd isn't installed yet. Use the 'Install steamcmd from "
                "AUR' button above (if you're on Outlaw OS) or set its path "
                "manually in the Config form.",
            )
            return
        cfg = self._read_form()
        if cfg.validate():
            QMessageBox.warning(self, "Fix config first", "Steam app_id and build account must be set.")
            return
        build_id = self.promote_build_edit.text().strip()
        branch = self.promote_branch_combo.currentText().strip()
        if not build_id.isdigit():
            QMessageBox.warning(self, "Build ID needed", "Enter a numeric BuildID to promote.")
            return
        if not branch:
            QMessageBox.warning(self, "Branch needed", "Pick or type a branch name (e.g. default, beta).")
            return

        confirm = QMessageBox.question(
            self,
            "Promote build?",
            f"Set the LIVE build of app {cfg.app_id} branch '{branch}' to BuildID {build_id}?\n\n"
            "This is what your players will get next time they launch the game.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        publisher = SteamPublisher(self._steamcmd_path, Path(self.workspace_root))
        worker = SteamPromoteWorker(publisher, cfg.app_id, build_id, branch, cfg.build_account)
        worker.line.connect(self._on_worker_line)
        worker.finished_with_result.connect(self._on_promote_finished)
        worker.finished.connect(worker.deleteLater)
        self._promote_worker = worker
        self.promote_btn.setEnabled(False)
        self._append_log("info", f"▶ Promoting BuildID {build_id} → {branch}…")
        worker.start()

    def _on_promote_finished(self, result) -> None:
        self._promote_worker = None
        self.promote_btn.setEnabled(True)
        if result.ok:
            self._append_log("ok", f"Promote OK. BuildID {result.build_id} is live on its branch.")
        else:
            self._append_log("error", f"Promote failed: {result.error}")

    # ------------------------------------------------------------------
    # UI bookkeeping
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self.upload_btn.setEnabled(not busy)
        self.preview_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.refresh_btn.setEnabled(not busy)
        self.save_btn.setEnabled(not busy)
        self.verify_btn.setEnabled(not busy)
        self.progress.setVisible(busy)

    def _append_log(self, level: str, msg: str) -> None:
        prefix = {
            "info":  ("info",  "·"),
            "ok":    ("ok",    "✔"),
            "warn":  ("warn",  "⚠"),
            "error": ("err",   "✕"),
        }.get(level, ("info", "·"))
        self.log_view.appendPlainText(f"{prefix[1]} {msg}")
        self.log_message.emit(level, msg)

    def _append_raw(self, line: str) -> None:
        self.log_view.appendPlainText(line)
