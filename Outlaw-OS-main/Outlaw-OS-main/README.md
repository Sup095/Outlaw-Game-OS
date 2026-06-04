# Outlaw OS v2.0

**A lightweight, secure, retro green-phosphor Linux desktop** built on Arch Linux,
tuned for **ethical hacking / security work** and **gaming + game development**.
The desktop is a hardened Electron shell; the ISO doubles as a live boot manager
and installer.

> v2.0 is a near-complete rewrite. The shell is now actually functional and
> secure (the v1 shell could not run a single command), arbitrary disk-wipes are
> guarded, the installer is real and supports **optional, non-destructive**
> installs + an uninstall path, the AI assistant is real and resource-light, and
> there is a one-press **hotswap** to your other OS.

---

## Highlights

- **Self-healing boot** — `outlaw-session-watchdog` wraps the chosen X
  session. Three consecutive crash-restarts (each under 60s) flip the OS into
  *safe mode* on the next boot: forces Desktop, drops a `~/.outlaw-safe-mode`
  marker, and the shell shows a banner explaining what happened. If desktop
  itself starts crash-looping, the watchdog drops you to a plain TTY so you
  can fix it from a shell. Counter clears the moment a session runs healthy.
- **Emergency stop** — `Ctrl+Alt+K` in either CodeMaker or the desktop shell
  hard-kills every running agent task, Steam worker, evolution pass, and
  every subprocess the shell spawned (pacman installs, terminal commands,
  etc.). Works through modal dialogs. Last-resort escape hatch.
- **Auto-snapshots** — every file the agent is about to write/append/delete/
  move is copied into `<project>/.outlaw/snapshots/<session>/<timestamp>/`
  before the change lands. New *Snapshots* tab in CodeMaker browses by
  session, shows side-by-side diffs, and offers per-file or whole-session
  restore (gated by the same approval dialog as a normal agent write).
  Retention capped at 200 snapshots / 100 MB per project, oldest pruned first.
- **Two sessions, one OS** — every boot opens a small **session greeter** with
  two cards: *Dev session* (launches **Outlaw CodeMaker**, the AI agent that
  builds Godot games with you) or *Desktop* (the standard hardened shell with
  files, browser, Apps panel, on-device AI). You can also flip mid-session via
  *Settings → Session → Switch now*. Bypass the greeter with
  `OUTLAW_SKIP_GREETER=1` for kiosks.
- **Works on low-end PCs** — CodeMaker ships with a built-in **VRAM saver**
  that reads NVML each turn and switches between *Full* / *Lean* / *Minimal*
  context modes automatically. Tight modes shrink the workspace tree, RAG
  hits, history slice, and max-output-tokens so the model never OOMs
  mid-generation — at the cost of needing more iterations to get the same
  amount of work done. A 4–6 GB card with a tiny Qwen / Llama running in
  LM Studio still produces full Godot games; it just goes slower. Mode shows
  in the status bar (green/amber/red); override in *Settings → VRAM saver*.
  The agent also **recalls past solved problems** via a CPU-only similarity
  search over its evolution log, so once a bug is fixed it doesn't have to
  be re-derived next time.
- **Crisp green-terminal UI by default** — no scanlines, no flicker, no glow
  bleeding over text. The retro CRT effect is still there, but it's an **opt-in
  toggle** in *Settings → Appearance*, so apps and games render exactly as
  intended with no discoloration.
- **Hardened by design**
  - Renderer runs sandboxed with `contextIsolation` on and `nodeIntegration`
    off. It never gets a raw shell — every privileged action is a named,
    validated IPC call.
  - A **destructive-command guard** intercepts things like `rm -rf /`, `dd`,
    `mkfs`, `wipefs`, fork bombs, etc., and forces you to type `CONFIRM` before
    they run — so you can't *accidentally* nuke the machine.
  - Strict Content-Security-Policy, no `eval`, external links open in the system
    browser, package signatures required (no `TrustAll`).
  - Installed systems use a real password + `wheel`/sudo (no passwordless root).
- **Real local AI assistant (off by default)** — routes prompts to a local
  [LM Studio](https://lmstudio.ai) server (`127.0.0.1:1234`). You pick the
  model in LM Studio (anything from a 0.5B tiny chat model up to whatever your
  VRAM can hold) and Outlaw uses it for OS-level tasks: open apps, search the
  web, list/open files, answer questions. The shell ships a thin
  `outlaw-lm-studio` launcher (in Settings → AI → *Open LM Studio*) that
  starts LM Studio if it's installed, or opens its download page if not.
  Nothing leaves the machine. Toggle the AI any time in Settings; OS boots
  **without AI** by default so it runs on low-VRAM machines.
- **Minimal default install** — the OS only pre-bundles what's needed to boot
  securely, run Steam, edit Godot projects, and talk to LM Studio. Everything
  else lives in the in-shell **Apps** panel and installs on demand (`pkexec
  pacman -S`, password-prompted). This keeps the ISO small and leaves more
  VRAM/RAM free for the AI.
- **Pre-installed:** Steam + steamcmd + the full Vulkan/32-bit stack
  (GameMode, Gamescope, MangoHud), Godot 4, Git, Python, base-devel, CMake,
  Firefox, file manager, terminal, the hardened shell. One-click **Performance
  mode** switches the CPU governor.
- **One click away (Apps panel):** Blender, GIMP, Krita, Inkscape, Aseprite,
  Tiled, Audacity, VS Code, Lutris, Wine, Discord, Chromium, LibreOffice,
  OBS Studio, VLC — plus an authorized-testing security toolkit (nmap,
  Wireshark, tcpdump, John, hashcat, sqlmap, aircrack-ng, hydra, netcat).
- **Hotswap** — one button (or `outlaw-hotswap`) sets the next boot to another
  installed OS (great for jumping to Windows) and reboots; if it can't find one,
  it drops you into the firmware boot menu.
- **Safe, optional installer** — install into a single empty partition without
  touching anything else, or (explicitly) erase a whole disk. Built-in
  **uninstall** removes only Outlaw OS so you can switch back to Windows/another
  OS without losing other data.

---

## Quick start (users)

1. Download the latest `outlaw-os-v2.0.iso` from **Releases**.
2. Verify it: `sha256sum -c outlaw-os-v2.0.iso.sha256`.
3. Write it to a USB stick (Rufus on Windows, or `dd if=outlaw-os-v2.0.iso of=/dev/sdX bs=4M status=progress` on Linux/Mac — pick the right device!).
4. Boot from the USB. You land in the live Outlaw desktop.
5. Try it live, or open **Settings → Open Installer** to install to disk.

### Installing safely
- **Recommended (keeps your other OS):** choose *“Install into an existing empty
  partition.”* Make the empty partition first (e.g. shrink Windows in Disk
  Management). Only that partition is formatted; everything else is untouched.
- **Full disk (destructive):** choose *“Erase an entire disk.”* You must type the
  device name **and** the word `ERASE`. Use with care.
- GRUB is installed with OS detection on, so your other systems appear in the
  boot menu automatically — which is what powers hotswap.

### Switching back / removing Outlaw OS
Boot the USB → **Open Installer → Remove Outlaw OS**, or run
`outlaw-install --uninstall`. It removes Outlaw's boot entry and *optionally*
frees its partition. Your other OS and its data stay intact.

---

## Using the desktop

| Area | What it does |
|------|--------------|
| **Dashboard** | System info + quick launchers |
| **Files** | Browse/open files (read-only listing, safe open) |
| **Task Monitor** | Live CPU/RAM + top processes |
| **Terminal** | Full shell, but destructive commands need `CONFIRM` |
| **Gaming** | Performance mode + Steam/Lutris launchers |
| **Game Dev** | Godot launcher + tips; install Blender/GIMP/VS Code/etc. from Apps |
| **Apps** | Curated on-demand installer (Game Dev tools, browsers, security, productivity) — runs `pkexec pacman -S` after a password prompt |
| **AI Assistant** | Routes prompts to local LM Studio; can open apps, search, list files |
| **Settings** | AI toggle, appearance (CRT/glow), updates, boot/hotswap |

**Hotswap:** the `⇄ HOTSWAP` button in the top bar (or `outlaw-hotswap`).

---

## For developers / builders

### Repository structure

```
.
├── outlaw-shell/                 # The desktop (Electron app) — single source of truth
│   ├── main.js                   #   Secure main process: IPC, command guard, AI + updater orchestration
│   ├── preload.js                #   contextBridge — the ONLY API the UI can touch
│   ├── ai-agent.js               #   LM Studio client (OpenAI-compatible) + tool-call parsing
│   ├── updater.js                #   GitHub Releases OTA updater (semver + SHA256)
│   ├── renderer.js               #   UI logic (CSP-safe, no inline handlers)
│   ├── index.html / styles.css   #   Markup + theme
│   └── package.json
├── outlaw-installer/             # archiso profile for the live ISO / boot manager
│   ├── build.sh                  #   Assembles profile from releng + overlays, builds ISO
│   ├── profiledef.sh             #   archiso profile metadata
│   ├── packages.x86_64           #   Package set (official repos only)
│   ├── pacman.conf               #   multilib on, signatures required
│   └── airootfs/                 #   Files baked into the image
│       ├── root/ , etc/skel/     #     autologin, picom config, .xinitrc → greeter → chosen session
│       └── usr/
│           ├── local/bin/        #     outlaw-greeter, outlaw-codemaker, outlaw-lm-studio,
│           │                     #     outlaw-install, outlaw-hotswap, outlaw-perf, outlaw-update-apply
│           └── share/
│               ├── outlaw-os/    #     bundled outlaw-shell (desktop session)
│               └── outlaw-greeter/   # tiny Electron app: Dev vs Desktop on every boot
└── .github/                      # CI build + FUNDING.yml (sponsor button)
```

### Boot flow

```
┌──────────┐    ┌─────────┐    ┌─────────────┐    ┌──────────────────┐
│  kernel  │ →  │ autologin│ →  │  startx     │ →  │  .xinitrc        │
└──────────┘    └─────────┘    └─────────────┘    └────────┬─────────┘
                                                            ▼
                                                  ┌──────────────────┐
                                                  │ outlaw-greeter   │  ←  Dev / Desktop
                                                  └────────┬─────────┘
                                                           ▼
                          ┌──────────────────────┬──────────────────────┐
                          ▼                      ▼                      ▼
                ┌──────────────────┐  ┌──────────────────┐    drop to TTY (rare)
                │ outlaw-codemaker │  │  outlaw-shell    │
                │ (PyQt + LM Studio)│  │  (Electron)      │
                │  /opt/outlaw-cm/ │  │ /usr/share/      │
                └──────────────────┘  │  outlaw-os/      │
                                      └──────────────────┘
```

The greeter writes the user's pick to `~/.outlaw-session`; `.xinitrc` reads
that file and `exec`s the chosen session. Mid-session, the desktop shell can
flip to the Dev session via *Settings → Session → Switch now* — it drops a
`~/.outlaw-session.honor-once` marker so the greeter skips its prompt that
once and the user lands directly in CodeMaker.

**Where Outlaw CodeMaker lives on disk:** `/opt/outlaw-codemaker/` is the
default location the launcher checks (it also tries `~/.local/share/outlaw-codemaker`
and `$OUTLAW_CODEMAKER_HOME`). The folder is expected to contain `main.py`
plus a Python venv at `.venv/`. If CodeMaker isn't found, the launcher
falls back to the desktop shell and shows a notification so the user is
never stranded.

### Bundling Outlaw CodeMaker into the ISO

`build.sh` calls `bundle-codemaker.sh` to copy the CodeMaker source tree
into `airootfs/opt/outlaw-codemaker/` before `mkarchiso` runs. It tries a
handful of default locations (sibling `../Outlaw CodeMaker`,
`../outlaw-codemaker`, etc.). If your checkout lives elsewhere, set
`OUTLAW_CODEMAKER_SRC`:

```bash
sudo OUTLAW_CODEMAKER_SRC=/abs/path/to/codemaker ./build.sh
```

If no source is found the bundle step is skipped (with a warning); the ISO
still builds and the greeter's Dev path falls back to the desktop shell at
boot, with a `notify-send` explaining how to install CodeMaker later.

The bundle script omits the venv (path-and-Python-version specific), local
SQLite DBs (the OS user starts fresh), the Windows-only Tesseract helper,
and the usual caches/build noise. `outlaw-install` re-creates the venv at
`/opt/outlaw-codemaker/.venv` during disk install via `python -m venv` +
`pip install -r requirements-linux.txt`. That step needs working internet
on the live USB and takes a few minutes the first time.

**Known limitations on Linux:** `vision/godot_bridge.py` uses `pygetwindow`,
which on X11 only partially works — screenshot/OCR features inside CodeMaker
degrade gracefully until that module is ported to `python-xlib`/`ewmh`. File
ops, LM Studio plumbing, the Project Picker / New Game wizard, the Roadmap
view, and the Steam panel all work fine on Linux.

### Run the desktop on your own machine (any OS)

```bash
cd outlaw-shell
npm install
npm start
```

It runs windowed off-Linux (kiosk only on the real OS) and degrades gracefully
where Linux-only telemetry isn't available — handy for UI work.

### Build the ISO (on Arch Linux)

```bash
sudo pacman -S archiso
cd outlaw-installer
sudo ./build.sh        # -> ../out/outlaw-os-v2.0.iso (+ .sha256)
```

`build.sh` copies the upstream `releng` profile as a base (so the BIOS/UEFI boot
scaffolding is always correct for your archiso version), overlays this repo's
package set, `airootfs`, and helper scripts, syncs `outlaw-shell/` into the
image, then runs `mkarchiso`.

> **Note:** building/booting the ISO must be done and tested on Linux (and ideally
> first in a VM). The Electron shell and the helper scripts are the parts you can
> exercise directly; the ISO assembly relies on a working `archiso` install.

---

## Shipping updates (Windows-style)

The shell self-updates from your own GitHub repo. Tag a release; users get a
"Update available" toast and a one-click install (with a password prompt — the
same pattern as Windows' UAC).

**Setup (once):** in **Settings → Outlaw Shell Updates**, fill in your repo as
`owner/repo` and leave *Background auto-check* on. Optionally fill the *Sponsor
URL* in **Support Development**.

**Releasing a new version:**
1. Push your changes to GitHub.
2. Tag and create a release, e.g. **v2.0.1**.
3. The included CI workflow runs automatically. It:
   - bumps `outlaw-shell/package.json` to match the tag,
   - bundles the shell as `outlaw-shell-v2.0.1.tar.gz`,
   - writes a `.sha256` checksum next to it,
   - builds the live ISO,
   - and attaches everything to the GitHub release.
4. Users running Outlaw OS get a toast within a few hours, or hit **Check for
   shell updates** in Settings. They click **Install update** → one
   password prompt → the helper verifies SHA256 again, atomically swaps
   `/usr/share/outlaw-os`, and keeps the previous version at
   `/usr/share/outlaw-os.prev` for rollback.

**Rollback:** if a release misbehaves,
`sudo mv /usr/share/outlaw-os.prev /usr/share/outlaw-os` restores the previous
version.

---

## Sponsor / donate button

Two complementary things:

1. **GitHub's "Sponsor" button** on the repo: edit
   [`.github/FUNDING.yml`](.github/FUNDING.yml) — uncomment the lines for the
   platforms you actually use. Fastest options:
   - **Ko-fi** (`ko_fi: your-handle`) — sign up at <https://ko-fi.com>, 5 minutes,
     no business setup.
   - **Buy Me a Coffee** (`buy_me_a_coffee: your-handle`) — same idea.
   - **GitHub Sponsors** (`github: [your-username]`) — apply at
     <https://github.com/sponsors>; requires Stripe.
   - **PayPal.me** (under `custom:`) — works with any PayPal account.
2. **In-shell button**: in Settings → Support Development, paste any URL into
   *Sponsor URL*. The "Open Sponsor Page" button in Settings opens it.

---

## Security & responsible use

The included offensive-security tools are for systems **you own or are explicitly
authorized to test**. Outlaw OS adds AppArmor, a UFW firewall, and `firejail`
sandboxing, plus the command guard described above — but ultimately you are
responsible for how you use it.

## License

MIT.
