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

Outlaw Game OS is built in the open, one phase at a time. Each **completed** roadmap collapses into a single line below; the **current** roadmap stays expanded with every phase, and when it's truly finished it becomes the next numbered release (v2.1).

```
Foundations   ▰▰▰▰▰▰▰▰▰▰  done — 4 roadmaps, all shipped & tested
This cycle    ▰▰▰▰▰▰▰▰▰▱▱▱▱▱▱  Completeness & Polish — phase 9 (Session driver profiles) underway → v2.1.0
```

> **Legend:** ✅ shipped & in your hands · 🚧 building now · 🔭 on the horizon

### ✅ Shipped & solid — completed roadmaps

| | Roadmap | What it delivered |
|---|---|---|
| 🧩 | **Product** | Merged the two original codebases — Project Picker, New Game wizard, single LM Studio backend, stripped install + Apps panel, boot greeter, Steam pipeline, VRAM saver. |
| 🛡 | **Reliability** | Auto-snapshots, Steam dry-run, emergency stop, LM Studio reconnect, prompt pre-flight, diagnostic dump, project templates, OS shell rollback. |
| 🛰 | **System Core** | The sci-fi centerpiece — live telemetry, three-tier diagnostics, scheduled checks, cold-mode dialogue + local TTS, Live AI with tool calls, two-layer VRAM tiering. |
| ⚙ | **Release & boot hardening** | Automated ISO build + publish on tag, live-ISO welcome card, GParted in the live environment, on-demand AUR install for `steamcmd`, the first-boot setup wizard, a boot crash-guard, and the long boot-anywhere fight (real hardware **and** VM). |
| 🔄 | **Updates, channels & control** | Package/driver updater · OS self-updater with one-click rollback · **stable / beta** channels · *Works / Broken* community testing · System Core **System Control** panel · CI package preflight · **Tune This PC** (hardware scan + thermal-watched stress test + auto-settings) · **low-VRAM CodeMaker** (spills the model into system RAM, right-sizes the model, caches context to disk). |

### 🚧 Completeness & Polish — current cycle *(the road to v2.1)*

The foundations boot and run; this cycle makes the desktop feel *finished* and rock-solid on real hardware. We keep iterating here through the `2.0.x` betas — when it's genuinely complete, this becomes the **v2.1** release. When a phase ships, the next moves to *building now*.

| | Phase | What you get |
|:--:|:--|:--|
| ✅ | **1 · Identity** | Optional **Gold Gunmetal** theme — the sci-fi-fortress look that matches Outlaw CodeMaker — switchable anytime in *Settings → Appearance*, on **both** the desktop and the dev tool. New source-available **license**. |
| ✅ | **2 · Your apps, found** | The Apps page has an **"On this PC"** view that auto-discovers everything you've installed — `.desktop` apps *and* AppImages you download — one click to launch, no manual refresh. |
| ✅ | **3 · Stabilization — make it fully functional** | The real-hardware shakedown: a **point-and-click installer** that shrinks Windows for you automatically, **Wi-Fi everywhere** (installer, first boot, Settings), the desktop **filling the whole screen**, **window management** (minimize/maximize/close + taskbar), a **4-digit PIN + sign-in**, an **updater that refreshes every component**, working **diagnostics + auto-tune**, correct **local time**, and a steady stream of fixes so *everything works end-to-end* before new features land. |
| ✅ | **4 · AI you can actually run** | Add **LM Studio** as a one-click download, plus a guided setup. A tiny model that runs on **any PC — even a weak one** — boots first and, knowing your hardware, walks you through everything: if your machine can handle more, it recommends a better model and the exact settings to use. The System Core can see your specs and tailor the advice. |
| ✅ | **5 · Task Manager** | A Windows-style task manager — **End task** / **End process tree**, a searchable/sortable process list (CPU%, memory) and live **CPU · RAM · GPU + VRAM** readouts, in the Outlaw look. |
| ✅ | **6 · Help + Quickstart** | A **skippable first-boot tour** showing where everything is, plus a searchable **Help database** that explains the whole OS and how to troubleshoot it. |
| ✅ | **7 · Reviewer that works** | One-click *"it worked / it broke"* reporting per version — opens a pre-filled GitHub issue with your system info (and an anonymous machine hash to merge duplicates), plus a live 👍/👎 community tally, so testing actually feeds back to the maintainer (and the stable channel). |
| ✅ | **8 · Boot sequence + pre-flight** | A cinematic boot every time: real system boot data scrolling on a green, CRT-scanline screen, with the holographic Outlaw sigil flickering to life (~3s) — skippable — at both the session greeter and the desktop's own boot screen, plus an optional **"check this PC"** pre-flight (CPU/RAM/GPU/disk + warnings) before you enter a session. |
| 🚧 | **9 · Session driver profiles** | Choose the driver/package stack per session — **dev-tuned** vs **gaming/desktop** — applied safely *after* boot, never touching the bootloader. |
| 🔭 | **10 · Window management polish** | Build on the windows + taskbar already shipped: keep the desktop pinned behind apps, themed title bars, window snapping, and remembered positions. |
| 🔭 | **11 · "Broken" mode** | A third theme where the system looks like it's *barely holding together* — glitches, scanlines, the System Core throwing (fake) errors. It's also the live-demo vibe: a broken machine you repair by installing. *(Basic live-mode feature-locks already shipped.)* |
| 🔭 | **12 · Loading screens** | A proper, styled loading screen for installs and other long jobs — one side shows the **phase**, the other a **live log** of what's happening. Glitches and tears in "Broken" mode. Replaces the plain installer for a far more polished look. |
| 🔭 | **13 · AI that runs your system (experimental)** | Test how far the on-device AI can go: tell it *"install Krita"* or *"update everything"* and it does it — whether the app is already installed, sitting on the Apps page, or it has to find and download it from the web. Outside the dev session VRAM is less precious, so it may even ship with a small built-in model so it works out of the box. Experimental — we're proving what's reliable before committing. |
| 🔭 | **14 · Self-update service (feasibility)** | An experiment in letting Outlaw keep itself — and its dependencies — current automatically, and quietly roll in fixes. We're testing whether it can be made reliable and safe; it may or may not stay. |
| 🔭 | **15 · Release readiness → v2.1.0** | A final fixing + **comprehensive testing** pass — including outstanding bugs like the installer's **automatic partition resizing** — to put the constant bugs to rest, leading to **v2.1.0 — the first fully-released version.** Updates continue after, but the rough-edges era ends here. |

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

Clicking *Install Outlaw OS* opens a **point-and-click installer wizard** — no terminal, no partitioning knowledge needed:

| Option | What it does | Other data |
|---|---|---|
| **1. Keep my other OS** *(recommended)* | Fully automatic: Outlaw uses free space on the disk — or **shrinks your biggest partition for you** (your files are kept; the partition just gets smaller). Pick the size with a slider. GRUB adds Windows / other Linux installs to the boot menu. | **Untouched.** Existing partitions are never formatted; shrinking is test-checked read-only first and refuses unsafe disks. |
| **2. Erase this computer** *(destructive)* | Wipes one whole disk and makes Outlaw the only OS on it. You type `ERASE` in the wizard to confirm. | Anything on the chosen disk is **gone**. Other disks are untouched. |

The wizard checks your internet connection up front, shows exactly what it's going to do before anything is touched, and streams live progress while it installs. (Prefer a terminal? `sudo outlaw-install` runs the same engine as text menus; `sudo gparted` is also still on the live ISO.)

> 💡 **Shrinking a Windows disk:** Windows must be cleanly shut down. If the wizard refuses, boot Windows, turn off *Fast Startup*, run `chkdsk /f`, shut down fully (don't restart), then try again. Windows runs a quick disk check on its next boot afterwards — that's normal.

You can also **try Outlaw without installing**. The live session is a limited preview — the full system (including the Dev session) unlocks once installed; your live changes disappear on reboot.

**Removing Outlaw OS later:** boot the USB again, open a terminal, and run `sudo outlaw-install --uninstall`. It deletes Outlaw's boot entry and (optionally) frees the partition. Your other OSes and their data stay intact.

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

## 🧪 Test in VirtualBox (community notes — not officially supported)

> ⚠️ **VirtualBox is no longer officially supported.** Outlaw is built and tested
> for **real hardware**. VirtualBox can work, but it's finicky — its emulated EFI
> firmware is buggy, and if Windows Hyper-V holds VT-x it falls back to a slow
> mode that hangs the boot. Even with the settings below it **may not run well or
> at all**, and we won't be troubleshooting VM-only problems. If you want to try
> it in a VM anyway, the notes below are the best starting point — but you're on
> your own getting it working. **Bare metal (a spare PC / USB boot) is the
> supported path.**

The settings below were the most reliable when VirtualBox *did* work:

> ### ⚠️ The single most important setting: **turn EFI OFF**
> VirtualBox's built-in **EFI firmware is buggy** and frequently hangs *before* it even loads the boot menu — you get a pure black screen and the machine "never does anything." This is a VirtualBox problem, not an Outlaw one (our ISO hasn't started running yet at that point). **Leave "Enable EFI" unchecked** so the VM boots in BIOS mode via syslinux, which is rock-solid. The Outlaw ISO supports both, but **BIOS is the reliable path in VirtualBox.**
>
> Already created the VM with EFI on and it black-screens? Either remake it with EFI off, or close VirtualBox and delete the VM's `*.nvram` file (next to its `.vbox` file) to clear the corrupted EFI state — then make sure EFI is unchecked.

> ### ⚠️ Just as important: let VirtualBox use **VT-x** (disable Hyper-V)
> If Windows is using **Hyper-V** — via *Core Isolation / Memory Integrity*, *Virtual Machine Platform*, *Windows Sandbox*, *WSL2*, or Docker Desktop — it **takes exclusive control of your CPU's VT-x**, so VirtualBox can't get it. VirtualBox then silently falls back to a slow, fragile **NEM** mode, and Linux guests **hang in early boot**: the kernel starts, brings up the CPUs, and freezes on one line for minutes — it looks exactly like the OS crashed, but the OS never really got to run.
> You can confirm it from the VM's log (`Machine → Show Log`, or the `VBox.log` next to the `.vbox` file): a line like
> `HM: HMR3Init: Attempting fall back to NEM: VT-x is not available` means Hyper-V has grabbed VT-x.
>
> **Fix:** open an **Administrator** PowerShell and run `bcdedit /set hypervisorlaunchtype off`, then reboot Windows. Also turn **off** *Windows Security → Device security → Core isolation → Memory integrity*. (To re-enable Hyper-V later: `bcdedit /set hypervisorlaunchtype auto`.) After the reboot, the VM log should show it's **using VT-x**, and boot is fast and reliable. If you can't disable Hyper-V, keep the VM at **2 CPUs** — NEM mode tolerates few cores far better than many.

**Create the VM:** VirtualBox → **New** → Name `Outlaw OS`, Type **Linux**, Version **Arch Linux (64-bit)**. Skip unattended install if asked (we use our own installer). Then open the VM's **Settings**:

| Section | Setting | Value | Why |
|---|---|---|---|
| **System → Motherboard** | **Enable EFI** | **❌ OFF (most important!)** | VirtualBox's EFI firmware can hang before boot. BIOS boot via syslinux is bulletproof. |
| **System → Motherboard** | Base Memory | **4096 MB+** (8192 recommended) | Electron + the installer need headroom |
| **System → Processor** | Processors | **2–4** | A couple of cores is plenty. **Avoid high counts (8+)** — especially if VirtualBox is in NEM/Hyper-V mode, many vCPUs can hang the guest at boot. |
| **Display → Screen** | Video Memory | **128 MB** | Avoids low-VRAM display glitches |
| **Display → Screen** | Graphics Controller | **VBoxVGA** *(most reliable)* or VMSVGA | VBoxVGA gives the most dependable framebuffer. If VMSVGA black-screens, switch to VBoxVGA. |
| **Display → Screen** | Enable 3D Acceleration | **❌ OFF** | Outlaw renders with software GL in VMs; 3D off is the reliable path. |
| **Storage** | Add Optical Drive → pick `outlaw-os-v*.iso` | — | The boot medium |
| **Storage** | Add a Hard Disk | **30 GB+** (only if you'll install) | Target for a real install |

Boot the VM → you land **directly on the Outlaw desktop**. To install to the virtual disk, click **Install Outlaw OS** — the point-and-click wizard opens (pick *Erase this computer* for the empty virtual disk). After install, power off, **remove the ISO**, and boot the disk.

### Troubleshooting a black screen, in order

1. **Pure black, never shows a boot menu, "nothing happens"** → VirtualBox EFI firmware hang. **Turn EFI OFF** (and delete the VM's `.nvram` file if it was on). This is the most common cause.
2. **Boot menu shows, kernel starts, then freezes on one line of text for minutes** → VirtualBox is running in **NEM / Hyper-V** mode because it can't get VT-x. **Disable Hyper-V** (see the VT-x box above) and/or drop the VM to **2 CPUs**. Confirm it in the VM log: a `fall back to NEM: VT-x is not available` line is the giveaway. *(This one isn't an Outlaw bug at all — the OS never gets to run.)*
3. **Boot menu shows, then black** → graphics mode. Set **Graphics Controller → VBoxVGA** and confirm **3D Acceleration is OFF**. (Outlaw uses the kernel's standard mode-setting driver with a VESA fallback, so a screen is found on either controller.)
4. **Desktop tries to start, then black** → switch to a text console with **Right-Ctrl + F2**, log in, and run `cat ~/.outlaw-x.log` — it records exactly what the graphical session did. (Outlaw also drops you to a usable shell automatically instead of looping.)

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

**Outlaw OS License v1.0** — source-available, *not* open-source. See [LICENSE](./LICENSE). In short:

- ✅ **You may** use it on your own machines, study the source, modify it locally, test pre-release builds, and **suggest changes** (issues / pull requests) — free, no permission needed.
- 🚫 **You may not** redistribute it (no re-hosting, mirrors, forks-for-download, or repackaged ISOs) or **monetize** it. The maintainer is the **sole distributor**.
- 💛 **Donations** are welcome and entirely optional — they buy no extra rights.

Bundled third-party components (the Linux kernel, Arch packages, Electron, Steam, Godot, Python libraries, fonts, etc.) keep their **own** upstream licenses; this license covers only Outlaw's own code, configuration, and branding.

---

## 🙏 Acknowledgements

Built on Arch Linux + Electron + PyQt6 + LM Studio + Godot Engine + Steam.
