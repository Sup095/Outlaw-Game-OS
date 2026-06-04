# Outlaw Game OS

**A lightweight, secure Linux desktop purpose-built for AI-driven Godot game development and Steam publishing.**

Two halves, one OS:

- **🛠 Outlaw CodeMaker** — a PyQt6 desktop AI agent that designs, codes, and ships Godot games. Local LM Studio backend. CPU-only RAG over your project. Auto-snapshots before every file write. Self-learning from past fixes. Steam upload + branch promotion built in. Runs in the **Dev session**.
- **⌂ Outlaw Shell** — a hardened Electron desktop with browser, file manager, terminal, Steam, **Apps panel** (on-demand pacman installs), and the sci-fi **System Core** — telemetry, diagnostics, scheduled background checks, local TTS voice, conversational AI, and tiered VRAM management. Runs in the **Desktop session**.

A small boot greeter asks **Dev or Desktop** on every boot; you can also flip mid-session.

---

## Highlights

- **Two sessions, one OS.** Boot into the greeter, pick Dev (CodeMaker) or Desktop (shell). Persistent preference if you always want the same one.
- **Self-healing boot.** Three quick crash-restarts of any session forces the next boot into safe-mode Desktop with a visible banner. Watchdog runs outside the shell — closing the desktop doesn't stop the safety net.
- **Auto-snapshots.** Every file the AI agent edits is captured first. Bad edit? Open the *Snapshots* tab and restore — through the same approval-diff as a normal write.
- **VRAM-friendly.** Two-layer model: always-on free savings (lazy imports, CPU-only RAG, paused-when-hidden pollers) plus conditional emergency mode (Auto / Off / Always Lean / Always Minimal). Works on 4–6 GB GPUs.
- **Sci-fi System Core.** Pure CSS/SVG centerpiece, zero VRAM idle. Live readouts (CPU, RAM, GPU+VRAM, disk, network, uptime, inventory). Three-tier diagnostic runner. Scheduled background checks via systemd user timers. Optional voice via piper-tts or espeak-ng. Optional Live AI mode with tightly allowlisted tool calls.
- **Steam publishing built in.** VDF generation, `steamcmd` upload, BuildID parsing, branch promotion. Mandatory dry-run preview before any real ship.
- **Hardened by design.** Sandboxed Electron renderer, context isolation, strict CSP, destructive-command guard, polkit-prompted privileged actions, no passwordless root. Emergency stop (Ctrl+Alt+K) anywhere.
- **Stripped default install.** ~30 packages on the ISO. Blender, GIMP, VS Code, Lutris, security toolkit etc. install on demand from the **Apps panel** (curated, pacman-backed, allowlisted).

---

## Repository layout

```
Outlaw-Game-OS/
├── Outlaw-OS-main/Outlaw-OS-main/
│   ├── outlaw-shell/        ← Electron desktop (Desktop session)
│   │   ├── main.js              IPC + sandbox + command guard
│   │   ├── preload.js           Audited contextBridge
│   │   ├── renderer.js          UI logic (CSP-safe)
│   │   ├── syscore.js           System Core state machine + lifecycle
│   │   ├── diagnostics.js       3-tier test runner
│   │   ├── diagnostics-cli.js   Standalone CLI for systemd timers
│   │   ├── coreai.js            System Core Live AI persona
│   │   ├── vram-tier.js         VRAM tier classifier
│   │   ├── tts.js               piper / espeak-ng wrapper
│   │   ├── dialogue.js/.json    Cold-mode catalogue + picker
│   │   ├── ai-agent.js          OS-level AI assistant
│   │   ├── updater.js           GitHub Releases OTA
│   │   ├── index.html / styles.css
│   │   └── package.json
│   └── outlaw-installer/    ← archiso profile + helper scripts
│       ├── build.sh             Builds the live ISO
│       ├── bundle-codemaker.sh  Bundles CodeMaker into airootfs
│       ├── packages.x86_64      ~30-package minimal install set
│       └── airootfs/            /etc/skel, /usr/local/bin, /usr/share, systemd units
└── Outlaw CodeMaker/        ← PyQt agent (Dev session)
    ├── main.py                  Entry point
    ├── config.json              LM Studio + paths
    ├── core/                    Orchestrator, snapshots, VRAM saver, Steam, etc.
    ├── ui/                      Project picker, New Game wizard, Snapshots tab, Steam panel
    ├── vision/                  Godot bridge (X11 + Windows backends)
    ├── db/                      Projects registry, vault (RAG), evolution log
    ├── tools/                   File controller, automation
    ├── assets/templates/        Empty / Platformer 2D / Top-down / 3D walker / VN
    ├── requirements.txt         Windows dev deps
    ├── requirements-linux.txt   Linux-on-OS deps (no pywin32)
    └── tests/                   pytest suite (153 tests)
```

---

## Quick start — three ways to run it

### 1. Preview the desktop shell on your dev machine (any OS)

```bash
cd "Outlaw-OS-main/Outlaw-OS-main/outlaw-shell"
npm install
npm start
```

The Electron app launches windowed. Linux-only features (apps:install, system:gpu-detailed, scheduled timers) degrade gracefully with "runs on Outlaw OS" notes — but you can navigate every screen, including the new System Core with its animations, dialogue, diagnostics UI, schedule rows, VRAM badge, and Live AI toggle.

### 2. Run Outlaw CodeMaker as a desktop app (Windows / Linux / macOS)

```bash
cd "Outlaw CodeMaker"
python -m venv .venv
.venv/Scripts/activate     # Windows
# source .venv/bin/activate # Linux/macOS
pip install -r requirements.txt
python main.py
```

CodeMaker expects LM Studio running locally on `127.0.0.1:1234` (load any model and click *Start Server*). The Project Picker opens on first launch; pick a Godot project or run the **New Game wizard**.

### 3. Boot the real OS (build the ISO + run in VirtualBox)

**Building the ISO requires Arch Linux** (the `mkarchiso` tool). The simplest path is to install Arch in a VirtualBox VM, clone this repo there, and build inside it. From an Arch host:

```bash
sudo pacman -S archiso git nodejs python python-pip
git clone https://github.com/YOUR-USERNAME/Outlaw-Game-OS.git
cd Outlaw-Game-OS/Outlaw-OS-main/Outlaw-OS-main/outlaw-installer
sudo OUTLAW_CODEMAKER_SRC=../../../"Outlaw CodeMaker" ./build.sh
# → ../out/outlaw-os-v2.0.iso
```

Then create a fresh VirtualBox VM (Arch Linux 64-bit, 6 GB RAM, 30 GB disk, EFI on, 128 MB VRAM, audio enabled), attach the ISO, and boot it. You'll land in the greeter — pick Desktop to test the System Core; pick Dev to test CodeMaker.

To install the OS to the VM's virtual disk instead of just booting live: from inside the shell, *Settings → Open Installer*. Or run `sudo outlaw-install` from a terminal.

---

## Architecture invariants (the things that should never break)

- **Sessions are isolated.** Dev (CodeMaker) and Desktop (shell) never run at the same time. The greeter routes between them.
- **System Core is desktop-only.** None of its files load in the Dev session. CodeMaker keeps every megabyte of VRAM for the model.
- **Zero-idle-cost.** When the System Core screen isn't visible, no timers tick, no animations run, no IPC fires, no LM Studio traffic. Same rule for the agent in CodeMaker — pollers pause when their widget is hidden.
- **Compositor-only animations.** Only `transform` and `opacity`. The System Core's three rotating rings never trigger layout or paint.
- **No new privileged IPC.** Every action the Live AI in the System Core can take routes through an existing audited IPC (apps:launch, diagnostics:run, etc.). The model can't reach a code path the user couldn't already trigger by hand.
- **TTS / animations gate on VRAM tier.** When `tier === minimal` (configured threshold or user-pinned), the System Core force-pauses animations and skips TTS calls even if the user toggled voice on. The text bubble still shows; we just don't fork piper under pressure.
- **Auto-snapshots before every agent write.** No exceptions. Restore is a regular write through the approval gate, which also snapshots — so you can undo a restore.

---

## Documentation map

This README is the entry point. Deeper docs live alongside the code:

- `Outlaw-OS-main/Outlaw-OS-main/README.md` — the shell + installer README (what to do with the ISO, the boot flow diagram, etc.).
- `Outlaw CodeMaker/assets/templates/README.md` — how project templates work.
- `Outlaw CodeMaker/core/vram_saver.py` — module docstring explains the two-layer VRAM model.
- `Outlaw CodeMaker/core/snapshots.py` — module docstring explains the on-disk format.
- `Outlaw CodeMaker/core/diagnostics.py` (and shell-side `outlaw-shell/diagnostics.js`) — the test catalogue with comments per tier.
- `Outlaw CodeMaker/tests/` — pytest suite (153 tests). Run with `.venv/Scripts/python.exe -m pytest tests/`.

---

## Roadmaps shipped

This codebase is the product of three sequential roadmaps:

1. **Product roadmap (7 slices)** — Merged the two original codebases. Project Picker + New Game wizard, single LM Studio backend, stripped OS default install + Apps panel, boot greeter, Steam pipeline, CodeMaker deployment to `/opt`, VRAM saver.
2. **Reliability roadmap (11 slices)** — Auto-snapshots, Steam dry-run, emergency stop, LM Studio reconnect, prompt pre-flight, diagnostic dump, greeter preference, Apps search + filter, "Working on…" headline + notifications, project templates, OS shell rollback UI.
3. **System Core roadmap (7 slices)** — Sci-fi centerpiece scaffold, live telemetry, three-tier diagnostics, scheduled background checks via systemd timers, cold-mode dialogue + local TTS, Live AI mode with tool calls, two-layer VRAM tiering.

All 25 slices are landed and tested. The `Outlaw CodeMaker/tests/` pytest suite plus the shell-side integration audits cover the critical paths.

---

## Contributing

Standard GitHub flow — fork, branch, PR. Before pushing:

```bash
# CodeMaker test suite
cd "Outlaw CodeMaker"
.venv/Scripts/python.exe -m pytest tests/

# Shell static check
cd ../Outlaw-OS-main/Outlaw-OS-main/outlaw-shell
node --check *.js
```

When adding a new shell IPC handler, make sure to:
1. Add it to `main.js` (`ipcMain.handle`).
2. Expose it in `preload.js` under the right namespace.
3. If it emits events, add the channel name to `EVENT_CHANNELS` in `preload.js`.
4. Add `.js` files to `outlaw-shell/package.json` `build.files` so electron-builder ships them.

When adding a new `/usr/local/bin/outlaw-*` script:
1. Drop it in `outlaw-installer/airootfs/usr/local/bin/`.
2. Add it to the `file_permissions` loop in `outlaw-installer/build.sh`.
3. Add it to the helper-copy loop in `outlaw-installer/airootfs/usr/local/bin/outlaw-install`.

---

## License

MIT. See [LICENSE](./LICENSE).

---

## Acknowledgements

Built on Arch Linux + Electron + PyQt6 + LM Studio + Godot Engine + Steam.

The retro gold-on-gunmetal aesthetic and the "sci-fi fortress AI" voice of the System Core are deliberate — Outlaw OS is meant to feel like a centuries-old machine that has seen everything and is mildly amused by you.
