# Outlaw Game OS

**A lightweight, secure Linux desktop purpose-built for AI-driven Godot game development and Steam publishing.**

Two halves, one OS:

- **🛠 Outlaw CodeMaker** — a PyQt6 desktop AI agent that designs, codes, and ships Godot games. Local LM Studio backend. CPU-only RAG over your project. Auto-snapshots before every file write. Self-learning from past fixes. Steam upload + branch promotion built in. Runs in the **Dev session**.
- **⌂ Outlaw Shell** — a hardened Electron desktop with browser, file manager, terminal, Steam, **Apps panel** (on-demand pacman installs), and the sci-fi **System Core** — telemetry, diagnostics, scheduled background checks, local TTS voice, conversational AI, and tiered VRAM management. Runs in the **Desktop session**.

Your **first boot goes straight to the Desktop** and runs a quick setup wizard (so you land on a known-good desktop and get everything installed). **After that**, a small boot greeter lets you pick **Dev or Desktop** each boot — and you can switch mid-session from Settings.

> **About the version number.** Releases are tagged `v2.0.x-beta`. The `2.0` isn't a claim to be a polished second major release — it's a **nod to the original Outlaw OS** this project grew out of, which was never fully finished. We kept the lineage in the number out of respect for where it all started. And the **`-beta` is honest**: Outlaw Game OS is **not finished or production-ready yet**. It's under active, rapid development — treat it as a work in progress, and expect rough edges.

> **A note from the team.** Outlaw Game OS is built by a small team, with the help of AI. We test everything we ship as thoroughly as we can — but we're a small group, so if something slips through, please bear with us and let us know on the [Issues](../../issues) page. We built this OS mainly **for ourselves**, the way we wanted it to work, and we're sharing it openly in case it's useful to anyone else who wants or needs something like it.

---

## ✨ Highlights

- **Two sessions, one OS.** First boot goes straight to Desktop (+ setup wizard) for a reliable start; after that a boot greeter lets you pick Dev (CodeMaker) or Desktop (shell), with a persistent preference if you always want the same one.
- **Sci-fi System Core.** Pure CSS/SVG centerpiece, zero VRAM idle. Live readouts (CPU, RAM, GPU+VRAM, disk, network, uptime, inventory), a three-tier diagnostic runner, scheduled background checks, optional local TTS voice, an optional Live AI mode with tightly allowlisted tool calls, and a one-click **System Control** panel (update, performance mode, diagnostics).
- **AI game-dev agent.** CodeMaker designs, writes, and ships Godot games against a local LM Studio model — auto-snapshotting every file write so a bad edit is one click from undone, and learning from past fixes.
- **Steam publishing built in.** VDF generation, `steamcmd` upload, BuildID parsing, branch promotion — with a mandatory dry-run preview before any real ship.
- **Built-in updaters + channels.** A one-button package/driver updater and an Outlaw OS self-updater that pulls new releases on your chosen **stable** or **beta** channel and swaps the shell in place, with one-click rollback.
- **VRAM-friendly.** Two-layer model: always-on free savings (lazy imports, CPU-only RAG, paused-when-hidden pollers) plus a conditional emergency mode. Works on 4–6 GB GPUs.
- **Hardened by design.** Sandboxed Electron renderer, context isolation, strict CSP, destructive-command guard, polkit-prompted privileged actions, no passwordless root, emergency stop (Ctrl+Alt+K) anywhere.
- **Self-healing + VM-safe boot.** A crash-guard drops you to a readable shell instead of an invisible loop if the desktop ever fails, and the ISO auto-configures itself to boot cleanly inside VirtualBox / QEMU.
- **Stripped, opt-in install.** A minimal base; everything heavier (Steam, Firefox, Godot, Blender, VS Code, Lutris…) is offered by the first-boot wizard or installed on demand from the curated **Apps panel**.

---

## 🗺 The Road So Far

Outlaw Game OS is built in the open, one phase at a time.

```
Foundations  ▰▰▰▰▰▰▰▰▰▰  done
This era      ▰▰▰▰▰▰▰▰▰▰  Phases 1–6 shipped — now testing + polishing
```

> **Legend:** ✅ shipped & in your hands · 🚧 building now · 🔭 on the horizon

### ✅ Shipped & solid

**🏗 The foundations** — three complete roadmaps, 25 slices, all landed and tested:

| | Roadmap | What it delivered |
|---|---|---|
| 🧩 | **Product** | Merged the two original codebases — Project Picker, New Game wizard, single LM Studio backend, stripped install + Apps panel, boot greeter, Steam pipeline, VRAM saver. |
| 🛡 | **Reliability** | Auto-snapshots, Steam dry-run, emergency stop, LM Studio reconnect, prompt pre-flight, diagnostic dump, project templates, OS shell rollback. |
| 🛰 | **System Core** | The sci-fi centerpiece — live telemetry, three-tier diagnostics, scheduled checks, cold-mode dialogue + local TTS, Live AI with tool calls, two-layer VRAM tiering. |

**⚙ Release & boot hardening** — automated ISO build + publish on tag, live-ISO welcome card, GParted in the live environment, a polished installer, on-demand AUR install for `steamcmd`, the first-boot setup wizard, and a boot crash-guard.

**🔄 Update system, channels & control**
- **Phase 1 · Updates & channels** — package/driver updater, OS self-updater (in-place swap + one-click rollback), and **stable / beta** release channels.
- **Phase 2 · Community testing & desktop control** — in-OS *Works / Broken* stability reporting, plus the System Core **System Control** panel.
- **Phase 3 · Boot anywhere** — the ISO now boots reliably on real hardware *and* inside VirtualBox/QEMU (correct login shell, auto framebuffer + software-GL fallback, self-reporting boot failures), instead of dropping to a black screen.
- **Phase 4 · Bulletproof builds** — a CI preflight validates every package name against the Arch repos *before* the 20-minute ISO build, so a typo, a renamed package, or one dropped from the repos fails in seconds with a clear message instead of deep inside the build.
- **Phase 5 · Tune This PC** — a Settings panel that scans your hardware, runs an optional thermal-watched CPU stress test, and applies the best settings for *your* machine: CPU governor, swappiness, zram on low-RAM rigs, dev-friendly file-watcher/mmap limits, and CodeMaker's VRAM default. One click to apply, one to reset.
- **Phase 6 · Low-VRAM CodeMaker** — the dev agent now runs on modest GPUs. On tight VRAM it recommends (and can apply via LM Studio) a GPU-offload split that **spills the model into system RAM**, suggests a right-sized model, and **caches recall/roadmap context to disk** so the AI keeps its working memory without re-stuffing RAM each turn.

### 🚧 Building now

*The planned roadmap is fully shipped — now in testing + polish, and open to what the community reports.*

### 🌌 Beyond

More project templates, deeper Godot integration, broader hardware support, and quality-of-life polish — steered by what testers report through the in-OS stability reporting. Ideas and bug reports are always welcome on the [Issues](../../issues) page.

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

**Removing Outlaw OS later:** boot the USB again and pick **Remove Outlaw OS**. It deletes Outlaw's boot entry and (optionally) frees the partition. Your other OSes and their data stay intact.

---

## 🔄 Keeping Outlaw OS updated

Once installed, you never need to re-download an ISO for normal updates. Two updaters live in the desktop shell:

1. **Update installed software** — keeps your apps and drivers current (`pacman -Syu` under the hood). One button, one password prompt.
2. **Update Outlaw OS** — checks GitHub for a newer Outlaw release and updates the shell in place. If an update ever misbehaves, **Roll back** restores the previous version instantly.

**Stable vs. beta channel** (in *Settings*):

- **Stable** *(default)* — only versions that have been tested and marked stable. Recommended for everyday use.
- **Beta** — the newest build of any kind, including untested ones. Pick this if you want to help test and report back via the in-OS *Works / Broken* buttons.

---

# 🔧 For builders, testers & maintainers

Everything below is for people who want to test in a VM, run the pieces from source, build the ISO themselves, or contribute. If you just want to *use* Outlaw OS, you're already done above.

---

## 🧪 Test in VirtualBox (recommended VM settings)

If you want to try Outlaw in a virtual machine first, **the VM settings matter** — wrong settings are the #1 cause of a black screen.

> ### ⚠️ The single most important setting: **turn EFI OFF**
> VirtualBox's built-in **EFI firmware is buggy** and frequently hangs *before* it even loads the boot menu — you get a pure black screen and the machine "never does anything." This is a VirtualBox problem, not an Outlaw one (our ISO hasn't started running yet at that point). **Leave "Enable EFI" unchecked** so the VM boots in BIOS mode via syslinux, which is rock-solid. The Outlaw ISO supports both, but **BIOS is the reliable path in VirtualBox.**
>
> Already created the VM with EFI on and it black-screens? Either remake it with EFI off, or close VirtualBox and delete the VM's `*.nvram` file (next to its `.vbox` file) to clear the corrupted EFI state — then make sure EFI is unchecked.

**Create the VM:** VirtualBox → **New** → Name `Outlaw OS`, Type **Linux**, Version **Arch Linux (64-bit)**. Skip unattended install if asked (we use our own installer). Then open the VM's **Settings**:

| Section | Setting | Value | Why |
|---|---|---|---|
| **System → Motherboard** | **Enable EFI** | **❌ OFF (most important!)** | VirtualBox's EFI firmware can hang before boot. BIOS boot via syslinux is bulletproof. |
| **System → Motherboard** | Base Memory | **4096 MB+** (8192 recommended) | Electron + the installer need headroom |
| **System → Processor** | Processors | **2+** | Smoother desktop |
| **Display → Screen** | Video Memory | **128 MB** | Avoids low-VRAM display glitches |
| **Display → Screen** | Graphics Controller | **VBoxVGA** *(most reliable)* or VMSVGA | VBoxVGA gives the most dependable framebuffer. If VMSVGA black-screens, switch to VBoxVGA. |
| **Display → Screen** | Enable 3D Acceleration | **❌ OFF** | Outlaw renders with software GL in VMs; 3D off is the reliable path. |
| **Storage** | Add Optical Drive → pick `outlaw-os-v*.iso` | — | The boot medium |
| **Storage** | Add a Hard Disk | **30 GB+** (only if you'll install) | Target for a real install |

Boot the VM → you land **directly on the Outlaw desktop**. To install to the virtual disk, click **Install Outlaw OS** (or run `sudo outlaw-install`). The installer auto-detects the VM and bakes the same display-compatibility settings into your installed system. After install, power off, **remove the ISO**, and boot the disk.

### Troubleshooting a black screen, in order

1. **Pure black, never shows a boot menu, "nothing happens"** → VirtualBox EFI firmware hang. **Turn EFI OFF** (and delete the VM's `.nvram` file if it was on). This is the most common cause.
2. **Boot menu shows, then black** → graphics mode. Set **Graphics Controller → VBoxVGA** and confirm **3D Acceleration is OFF**. (The ISO already boots with `nomodeset` + a framebuffer X driver to help.)
3. **Desktop tries to start, then black** → switch to a text console with **Right-Ctrl + F2**, log in, and run `cat ~/.outlaw-x.log` — it records exactly what the graphical session did. (Outlaw also drops you to a usable shell automatically instead of looping.)

---

## 💻 Run the pieces from source (dev preview, any OS)

**The desktop shell:**

```bash
cd "Outlaw-OS-main/Outlaw-OS-main/outlaw-shell"
npm install
npm start
```

The Electron app launches windowed. Linux-only features degrade gracefully with "runs on Outlaw OS" notes — but you can navigate every screen, including the System Core.

**Outlaw CodeMaker:**

```bash
cd "Outlaw CodeMaker"
python -m venv .venv
.venv/Scripts/activate     # Windows
# source .venv/bin/activate # Linux/macOS
pip install -r requirements.txt
python main.py
```

CodeMaker expects LM Studio running locally on `127.0.0.1:1234` (load any model and click *Start Server*). The Project Picker opens on first launch; pick a Godot project or run the **New Game wizard**.

---

## 🏭 Build the ISO yourself on Arch Linux

Normal releases are built automatically by GitHub Actions on every version tag, so you rarely need this. To build locally you need **Arch Linux** (for `mkarchiso`). No Arch machine? Install one in a VM first ([Arch install guide](https://wiki.archlinux.org/title/Installation_guide)), then:

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

## 🗂 Repository layout

```
Outlaw-Game-OS/
├── Outlaw-OS-main/Outlaw-OS-main/
│   ├── outlaw-shell/        ← Electron desktop (Desktop session)
│   │   ├── main.js              IPC + sandbox + command guard
│   │   ├── preload.js           Audited contextBridge
│   │   ├── renderer.js          UI logic (CSP-safe)
│   │   ├── syscore.js           System Core state machine + lifecycle
│   │   ├── diagnostics.js       3-tier test runner
│   │   ├── coreai.js            System Core Live AI persona
│   │   ├── vram-tier.js         VRAM tier classifier
│   │   ├── tts.js               piper / espeak-ng wrapper
│   │   ├── updater.js           GitHub Releases OTA (stable/beta channels)
│   │   ├── index.html / styles.css
│   │   └── package.json
│   ├── outlaw-firstboot/    ← first-boot setup wizard (Electron)
│   └── outlaw-installer/    ← archiso profile + helper scripts
│       ├── build.sh             Builds the live ISO + self-update component tarball
│       ├── bundle-codemaker.sh  Bundles CodeMaker into airootfs
│       ├── packages.x86_64      Minimal live-ISO install set
│       └── airootfs/            /etc/skel, /usr/local/bin (outlaw-* helpers), systemd units
└── Outlaw CodeMaker/        ← PyQt agent (Dev session)
    ├── main.py                  Entry point
    ├── core/                    Orchestrator, snapshots, VRAM saver, Steam, etc.
    ├── ui/                      Project picker, New Game wizard, Snapshots tab, Steam panel
    ├── vision/                  Godot bridge (X11 + Windows backends)
    ├── db/                      Projects registry, vault (RAG), evolution log
    ├── assets/templates/        Empty / Platformer 2D / Top-down / 3D walker / VN
    └── tests/                   pytest suite
```

---

## 🧱 Architecture invariants (the things that should never break)

- **Sessions are isolated.** Dev (CodeMaker) and Desktop (shell) never run at the same time. The greeter routes between them.
- **System Core is desktop-only.** None of its files load in the Dev session. CodeMaker keeps every megabyte of VRAM for the model.
- **Zero-idle-cost.** When the System Core screen isn't visible, no timers tick, no animations run, no IPC fires, no LM Studio traffic. Same rule for the agent in CodeMaker — pollers pause when their widget is hidden.
- **Compositor-only animations.** Only `transform` and `opacity`. The System Core's rotating rings never trigger layout or paint.
- **No new privileged IPC.** Every action the Live AI can take routes through an existing audited IPC. The model can't reach a code path the user couldn't already trigger by hand.
- **TTS / animations gate on VRAM tier.** Under `minimal` tier the System Core force-pauses animations and skips TTS even if voice is on.
- **Auto-snapshots before every agent write.** No exceptions. Restore is itself a snapshotted write, so you can undo a restore.

---

## 📦 Shipping a new release (maintainers)

A GitHub Actions workflow at `.github/workflows/build-iso.yml` builds the ISO and attaches it to a Release whenever you push a version tag:

```bash
# Bump ISO_VERSION in outlaw-installer/build.sh (+ profiledef.sh + outlaw-shell/package.json), commit,
git tag V2.0.x-Beta
git push origin main --tags
```

The workflow takes ~15–25 minutes. When it finishes, the matching Release page has the `.iso`, its `.sha256`, and the `outlaw-shell-v*.tar.gz` self-update component attached.

**Marking a version stable:** every CI build is published as a GitHub *pre-release* (the beta channel). When you've tested one and it's solid, open that release on GitHub and **uncheck "Set as a pre-release"** — it instantly becomes the stable target for everyone on the stable channel. You can do this to any past version, any time.

You can also kick off a manual build from the Actions tab (workflow_dispatch) — that produces a downloadable artifact without publishing a Release.

---

## 🤝 Contributing

Standard GitHub flow — fork, branch, PR. Before pushing, sanity-check what you touched:

```bash
# Shell scripts parse
bash -n outlaw-installer/airootfs/usr/local/bin/outlaw-*

# Shell JS parses
cd Outlaw-OS-main/Outlaw-OS-main/outlaw-shell && node --check *.js

# CodeMaker test suite
cd "Outlaw CodeMaker" && .venv/Scripts/python.exe -m pytest tests/
```

When adding a new shell IPC handler: add it to `main.js` (`ipcMain.handle`), expose it in `preload.js` under the right namespace, register any event channel in `EVENT_CHANNELS`, and add new `.js` files to `package.json` `build.files`.

When adding a new `/usr/local/bin/outlaw-*` script: drop it in `outlaw-installer/airootfs/usr/local/bin/`, add it to the `file_permissions` loop in `build.sh`, and note that the installer copies every `outlaw-*` helper to installed systems automatically.

---

## 📄 License

MIT. See [LICENSE](./LICENSE).

---

## 🙏 Acknowledgements

Built on Arch Linux + Electron + PyQt6 + LM Studio + Godot Engine + Steam.

The retro gold-on-gunmetal aesthetic and the "sci-fi fortress AI" voice of the System Core are deliberate — Outlaw OS is meant to feel like a centuries-old machine that has seen everything and is mildly amused by you.
