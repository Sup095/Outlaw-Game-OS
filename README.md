# Outlaw Game OS

**A lightweight, secure Linux desktop purpose-built for AI-driven Godot game development and Steam publishing.**

Two halves, one OS:

- **🛠 Outlaw CodeMaker** — a PyQt6 desktop AI agent that designs, codes, and ships Godot games. Local LM Studio backend. CPU-only RAG over your project. Auto-snapshots before every file write. Self-learning from past fixes. Steam upload + branch promotion built in. Runs in the **Dev session**.
- **⌂ Outlaw Shell** — a hardened Electron desktop with browser, file manager, terminal, Steam, **Apps panel** (on-demand pacman installs), and the sci-fi **System Core** — telemetry, diagnostics, scheduled background checks, local TTS voice, conversational AI, and tiered VRAM management. Runs in the **Desktop session**.

Your **first boot goes straight to the Desktop** and runs a quick setup wizard (so you land on a known-good desktop and get everything installed). **After that**, a small boot greeter lets you pick **Dev or Desktop** each boot — and you can switch mid-session from Settings.

---

## 🚀 Install on a real computer in 3 steps

You don't need to build anything. Just grab the ISO and go.

### 1. Download the ISO

Go to the [**Releases**](../../releases) page on this repository and click the latest `outlaw-os-v*.iso` file. *(Tip: download the matching `.sha256` next to it if you want to verify the file is intact — `sha256sum -c outlaw-os-v*.iso.sha256`.)*

### 2. Burn it to a USB stick (8 GB or larger)

- **On Windows:** Install [Rufus](https://rufus.ie/), pick the ISO, pick your USB drive, leave defaults, click *Start*. ~5 minutes.
- **On macOS:** `diskutil list` to find your USB device, then `sudo dd if=outlaw-os-v*.iso of=/dev/diskN bs=4m` (replace `N` with your device number — **double-check it's the USB, not your hard drive!**).
- **On Linux:** `sudo dd if=outlaw-os-v*.iso of=/dev/sdX bs=4M status=progress` (replace `sdX` with your USB device).

### 3. Boot from the USB

Reboot, hit the boot-menu key during POST (usually **F12 / F2 / Del / Esc** — varies by motherboard), pick the USB drive. You'll land in the live Outlaw OS desktop with a sci-fi pulsing core in the middle of the screen.

A welcome card on the Dashboard asks: **Install Outlaw OS** · **Try it first** · **Don't show again**.

### Two install paths — your choice

When you click *Install Outlaw OS*, you're asked how:

| Option | What it does | Other data |
|---|---|---|
| **1. Install alongside other OSes** *(recommended)* | Outlaw goes into a single empty partition you pick. GRUB picks up Windows / other Linux installs and adds them to the boot menu. | **Untouched.** Anything not on the chosen partition stays exactly as it was. |
| **2. Erase a whole disk** *(destructive)* | Wipes every partition on the chosen drive, makes Outlaw the only OS on it. You type the device name *and* the word `ERASE` to confirm. | Anything on the chosen disk is **gone**. Other disks are untouched. |

**Don't have an empty partition yet?** Open a terminal in the live system and run `sudo gparted` — it's pre-installed in the live ISO. Shrink Windows (or any other partition), make a fresh blank partition in the freed space, then run the installer.

You can also **try Outlaw without installing**. Everything works — diagnostics, the System Core, the shell, CodeMaker — your changes just disappear on reboot.

### Removing Outlaw OS later

If you want to switch back to your other OS, boot the USB again and pick **Remove Outlaw OS**. It deletes Outlaw's boot entry and (optionally) frees the partition. Your other OSes and their data stay intact.

---

## 🔄 Keeping Outlaw OS updated

Once installed, you never need to re-download an ISO for normal updates. Two updaters live in the desktop shell:

1. **Update installed software** — keeps your apps and drivers current (`pacman -Syu` under the hood). One button, one password prompt.
2. **Update Outlaw OS** — checks GitHub for a newer Outlaw release and updates the shell in place. If an update ever misbehaves, **Roll back** restores the previous version instantly.

**Stable vs. beta channel** (in *Settings*):

- **Stable** *(default)* — only versions that have been tested and marked stable. Recommended for everyday use.
- **Beta** — the newest build of any kind, including untested ones. Pick this if you want to help test and report back.

> **For maintainers:** every CI build is published as a GitHub *pre-release* (the beta channel). When you've tested one and it's solid, open that release on GitHub and **uncheck "Set as a pre-release"** — it instantly becomes the stable target that everyone on the stable channel receives. You can do this to any past version at any time.

---

## Highlights

- **Two sessions, one OS.** First boot goes straight to Desktop (+ setup wizard) for a reliable start; after that a boot greeter lets you pick Dev (CodeMaker) or Desktop (shell), with a persistent preference if you always want the same one.
- **Self-healing boot.** Three quick crash-restarts of any session forces the next boot into safe-mode Desktop with a visible banner. Watchdog runs outside the shell — closing the desktop doesn't stop the safety net.
- **Auto-snapshots.** Every file the AI agent edits is captured first. Bad edit? Open the *Snapshots* tab and restore — through the same approval-diff as a normal write.
- **VRAM-friendly.** Two-layer model: always-on free savings (lazy imports, CPU-only RAG, paused-when-hidden pollers) plus conditional emergency mode (Auto / Off / Always Lean / Always Minimal). Works on 4–6 GB GPUs.
- **Sci-fi System Core.** Pure CSS/SVG centerpiece, zero VRAM idle. Live readouts (CPU, RAM, GPU+VRAM, disk, network, uptime, inventory). Three-tier diagnostic runner. Scheduled background checks via systemd user timers. Optional voice via piper-tts or espeak-ng. Optional Live AI mode with tightly allowlisted tool calls.
- **Steam publishing built in.** VDF generation, `steamcmd` upload, BuildID parsing, branch promotion. Mandatory dry-run preview before any real ship.
- **Hardened by design.** Sandboxed Electron renderer, context isolation, strict CSP, destructive-command guard, polkit-prompted privileged actions, no passwordless root. Emergency stop (Ctrl+Alt+K) anywhere.
- **Stripped default install.** Minimal package set on the ISO. Blender, GIMP, VS Code, Lutris, security toolkit etc. install on demand from the **Apps panel** (curated, pacman-backed, allowlisted).
- **First-boot setup wizard.** The first time you log in after installing, a one-screen wizard offers the optional bundles (Steam stack, Firefox, Godot) as checkboxes — install what you want, skip the rest. It never appears again.
- **Built-in updaters.** A package/driver updater (one button → `pacman -Syu`) and an Outlaw OS self-updater that pulls new releases from GitHub on your chosen channel (**stable** or **beta**) and swaps the shell in place, with one-click rollback if a release misbehaves.
- **Runs in a VM out of the box.** Auto-detects VirtualBox / QEMU and forces software rendering so the desktop never black-screens on a VM without 3D acceleration. A boot-time crash-guard drops you to a readable shell instead of an invisible getty loop if X ever fails.

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
│   │   ├── updater.js           GitHub Releases OTA (stable/beta channels)
│   │   ├── index.html / styles.css
│   │   └── package.json
│   ├── outlaw-firstboot/    ← first-boot setup wizard (Electron)
│   │   ├── main.js              Bundle install via pkexec pacman
│   │   ├── preload.js / renderer.js / index.html / styles.css
│   │   └── package.json
│   └── outlaw-installer/    ← archiso profile + helper scripts
│       ├── build.sh             Builds the live ISO + self-update component tarball
│       ├── bundle-codemaker.sh  Bundles CodeMaker into airootfs
│       ├── packages.x86_64      Minimal live-ISO install set
│       └── airootfs/            /etc/skel, /usr/local/bin (outlaw-* helpers), systemd units
├── tests/
│   └── check-all.sh         ← repo-wide static/security/consistency suite (CI gate)
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

### 3. Test the real OS in VirtualBox (recommended VM settings)

Most people just download the ISO from Releases (above) and use it. If you want to test it in a virtual machine first, **the VM settings matter** — wrong settings are the #1 cause of a black screen. Use these exactly:

**Create the VM:**

1. Open VirtualBox → **New**.
2. **Name:** Outlaw OS · **Type:** Linux · **Version:** *Arch Linux (64-bit)*. (This is the "Arch Linux setup" — VirtualBox just needs to know it's a 64-bit Linux guest; it doesn't change the ISO.)
3. **Skip unattended install** if asked (we use our own installer).

**Then open the VM's Settings and set:**

| Section | Setting | Value | Why |
|---|---|---|---|
| **System → Motherboard** | Base Memory | **4096 MB+** (8192 recommended) | Electron + the installer need headroom |
| **System → Motherboard** | Enable **EFI** | ✅ on | Outlaw boots in UEFI mode |
| **System → Processor** | Processors | **2+** | Smoother desktop |
| **Display → Screen** | Video Memory | **128 MB** | Avoids low-VRAM display glitches |
| **Display → Screen** | Graphics Controller | **VMSVGA** | The controller Outlaw's `vesa`/modesetting drivers target |
| **Display → Screen** | Enable 3D Acceleration | **❌ OFF** | Outlaw auto-uses software rendering in VMs; leaving 3D off is the reliable path |
| **Storage** | Add Optical Drive → pick `outlaw-os-v*.iso` | — | The boot medium |
| **Storage** | Add a Hard Disk | **30 GB+** (only if you'll install) | Target for a real install |

4. Boot the VM. You'll land **directly on the Outlaw desktop** with the pulsing System Core. A welcome card offers **Install Outlaw OS · Try it first · Don't show again**.
5. To install to the VM's virtual disk: click **Install Outlaw OS** (or run `sudo outlaw-install` in a terminal). After install, power off, **remove the ISO from the optical drive**, and boot the disk.
6. On the installed system's **first boot** you go straight to the Desktop and a setup wizard offers the optional bundles (Steam / Firefox / Godot). After that, every boot shows the greeter to pick Dev or Desktop.

> **Still get a black screen?** Switch to a text console with **Right-Ctrl + F2**, log in, and run `cat ~/.outlaw-x.log` — it records exactly what the graphical session did. (Outlaw also drops you to a usable shell automatically if the desktop fails to start, instead of looping.)

### 4. Build the ISO yourself on Arch Linux (optional — for contributors)

Normal releases are built automatically by GitHub Actions on every version tag, so you rarely need this. But to build locally you need **Arch Linux** (for `mkarchiso`). If you don't have an Arch machine, install one in a VM first ([Arch install guide](https://wiki.archlinux.org/title/Installation_guide)), then:

```bash
# On an Arch Linux host, as a normal user:
sudo pacman -Syu --needed archiso git nodejs python python-pip

git clone https://github.com/Sup095/Outlaw-Game-OS.git
cd Outlaw-Game-OS/Outlaw-OS-main/Outlaw-OS-main/outlaw-installer

# build.sh must run as root (mkarchiso needs loop devices + chroot).
# OUTLAW_CODEMAKER_SRC points at the CodeMaker source so it gets bundled in.
sudo OUTLAW_CODEMAKER_SRC="../../../Outlaw CodeMaker" ./build.sh
# → ../out/outlaw-os-v<version>.iso  (+ .sha256)
```

The build takes ~15–25 minutes (mostly `pacstrap` + squashfs compression) and prints the final ISO size with a 2 GB budget warning.

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

## 🗺 Roadmap

Outlaw OS is built in phases. This section is kept up to date as work lands — check the boxes to see what's done.

### Foundations — ✅ shipped

The product is the result of three completed roadmaps (25 slices, all landed and tested):

- ✅ **Product roadmap (7 slices)** — Merged the two original codebases: Project Picker + New Game wizard, single LM Studio backend, stripped OS default install + Apps panel, boot greeter, Steam pipeline, CodeMaker deployment to `/opt`, VRAM saver.
- ✅ **Reliability roadmap (11 slices)** — Auto-snapshots, Steam dry-run, emergency stop, LM Studio reconnect, prompt pre-flight, diagnostic dump, greeter preference, Apps search + filter, "Working on…" headline + notifications, project templates, OS shell rollback UI.
- ✅ **System Core roadmap (7 slices)** — Sci-fi centerpiece scaffold, live telemetry, three-tier diagnostics, scheduled background checks via systemd timers, cold-mode dialogue + local TTS, Live AI mode with tool calls, two-layer VRAM tiering.

### Release & boot hardening — ✅ shipped

- ✅ Automated ISO build + publish on tag push (GitHub Actions).
- ✅ Live-ISO welcome card, GParted in the live environment, polished installer.
- ✅ On-demand AUR install for `steamcmd` (Steam panel installs it at first publish).
- ✅ VirtualBox/VM black-screen fix (auto software rendering + guaranteed video driver).
- ✅ First-boot setup wizard (opt-in Steam / Firefox / Godot bundles).
- ✅ Boot crash-guard (escape to a readable shell instead of an invisible loop).

### Current work

- 🚧 **Phase 1 — Update system + channels.** Package/driver updater, Outlaw OS self-updater (in-place component swap + rollback), and stable/beta release channels. *Backend done; in-OS Updates panel UI in progress.*
- 🔜 **Phase 2 — Community testing + desktop features + hardening.** In-OS "Works / Broken" stability reporting (advisory; maintainer blesses stable), a desktop System Core **Chat + system-control** panel, and a repo-wide **security + stress test suite** wired into CI.

### Planned

- 🔜 **Phase 3 — CI package preflight.** Validate every package name exists in the Arch repos *before* the 20-minute ISO build, so a typo or removed package fails in seconds with a clear message.
- 🔜 **Phase 4 — Post-install stress test + auto-tune.** After install, run a hardware check / stress test, then automatically apply the best settings for *your* machine (CPU governor, swap/zram, GPU power mode, VRAM defaults, compositor, file-watcher limits) to make development smoother.
- 🔜 **Phase 5 — CodeMaker / dev overhaul.** A big upgrade to the development experience while keeping background VRAM minimal — including a low-VRAM mode that runs a smaller model, spills into system RAM when needed (slower but better code), and caches roadmaps/context to disk so the AI keeps its memory without using more RAM.

### Future updates

Beyond the planned phases, Outlaw OS will keep improving — more project templates, deeper Godot integration, broader hardware support, and quality-of-life polish driven by what testers report through the in-OS stability reporting. Suggestions and bug reports are welcome on the [Issues](../../issues) page.

---

## Shipping a new release (maintainers)

This repo has a GitHub Actions workflow at `.github/workflows/build-iso.yml` that builds the ISO automatically and attaches it to a Release whenever you push a version tag. The flow:

```bash
# Bump the version in outlaw-installer/build.sh (ISO_VERSION="2.1.0") + commit it
git commit -am "Bump to v2.1.0"

# Tag and push
git tag v2.1.0
git push origin main --tags
```

That's it. The workflow takes ~15–25 minutes (mostly pacstrap + squashfs). When it finishes, the matching Release page has `outlaw-os-v2.1.0.iso` + `.sha256` attached, ready for users to download.

You can also kick off a manual build from the Actions tab (workflow_dispatch) — that produces a downloadable artifact without publishing a Release.

---

## Contributing

Standard GitHub flow — fork, branch, PR. Before pushing, run the repo-wide check suite (the same one CI runs on every push — see `.github/workflows/checks.yml`):

```bash
# Static + security + consistency checks across the whole repo:
# every script/JS/Python parses, Electron hardening intact, no secrets,
# versions agree, helper files exist. Needs bash + node + python3 (and
# shellcheck for the lint stage; it's skipped with a note if absent).
bash tests/check-all.sh

# CodeMaker test suite
cd "Outlaw CodeMaker"
.venv/Scripts/python.exe -m pytest tests/
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
