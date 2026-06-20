// ============================================================================
// Outlaw OS - Electron main process (secure)
// ----------------------------------------------------------------------------
// Hardened for a security/gaming desktop:
//   * contextIsolation ON, nodeIntegration OFF, sandboxed renderer
//   * the renderer never gets a raw shell; every privileged action is a named,
//     validated IPC handler
//   * a destructive-command guard makes it hard to accidentally wipe the disk
//   * external navigation is intercepted and handed to the system browser
// Degrades gracefully on non-Linux hosts so the UI can be previewed anywhere.
// ============================================================================

const { app, BrowserWindow, ipcMain, shell, screen } = require('electron');
const { spawn, execFile } = require('child_process');

// Phase 10: give the shell a stable WM_CLASS so the window manager (openbox)
// can pin it to the "below" layer — the desktop stays behind launched apps.
// Must be set before the app is ready. Harmless off-Linux.
try { app.commandLine.appendSwitch('class', 'outlaw-shell'); } catch { /* older electron */ }
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const aiAgent = require('./ai-agent');
const updater = require('./updater');
const APP_VERSION = require('./package.json').version;
const { DiagnosticRunner, listReports: listDiagReports, readReport: readDiagReport } = require('./diagnostics');
const tts = require('./tts');
const { VramTierMonitor } = require('./vram-tier');
const coreai = require('./coreai');

const IS_LINUX = process.platform === 'linux';
// Phase 13.2 — local AI backends (both OpenAI-compatible). The BUILT-IN base AI
// is a bundled Ollama model that runs on almost anything; the fallback is the
// user's own LM Studio. The Dev session never touches either — it has its own
// backend — so this stays desktop-only per the Dev⟂Desktop rule.
const LM_STUDIO_V1 = 'http://127.0.0.1:1234/v1';
const OLLAMA_V1 = 'http://127.0.0.1:11434/v1';
const BASE_AI_MODEL = 'qwen2.5:1.5b';   // small enough for anything, capable enough to be useful
let mainWindow = null;
let autoCheckTimer = null;

// Set of long-running subprocesses we've spawned (apps:install, updates:apply,
// terminal:run, etc.). Emergency stop (Ctrl+Alt+K) walks this and kills them
// all — last-resort escape hatch when one of them is hung.
const trackedProcs = new Set();

// SC3 System Core diagnostics. Instantiated lazily (after runShell is in scope
// at module load time) and reused across runs. Progress events flow to the
// renderer via the 'diagnostics-progress' channel; if no mainWindow exists
// yet, events are dropped — the renderer can call diagnostics:status to
// resync the next time it opens the System Core screen.
let _diagRunner = null;
function getDiagRunner() {
    if (_diagRunner) return _diagRunner;
    _diagRunner = new DiagnosticRunner({ runShell });
    _diagRunner.on('progress', (payload) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('diagnostics-progress', payload);
        }
    });
    return _diagRunner;
}

// SC7 VRAM tier monitor. One instance for the whole shell process. Background
// polling kicks off when the first renderer subscribes (we treat window
// creation as the implicit subscription) so a shell launched purely to handle
// IPC from a script doesn't fork nvidia-smi every 10s for nobody.
const vramTier = new VramTierMonitor();
// Apply the persisted mode on boot so the very first tier read reflects the
// user's pref instead of the constructor default ('auto').
try { vramTier.setMode(settings.vramSaverMode || 'auto'); }
catch (e) { console.warn('vramTier initial setMode rejected:', e.message); }
vramTier.on('tier-changed', (status) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('vram-tier-changed', status);
    }
});

// ---------------------------------------------------------------------------
// Settings (persisted JSON in userData)
// ---------------------------------------------------------------------------
const SETTINGS_PATH = path.join(app.getPath('userData'), 'settings.json');
const DEFAULT_SETTINGS = {
    // Phase 13.2 — AI is ON by default now that there's a bundled built-in model
    // (no setup needed). The System Core + AI Assistant use it automatically; the
    // tiny model loads on demand and unloads when idle, and the Dev session never
    // touches it. "Start without AI" at boot, or this toggle, turns it off.
    aiEnabled: true,
    // 'local-model' is LM Studio's sentinel — it routes to whatever model the
    // user has loaded in LM Studio's UI. Matches Outlaw CodeMaker's default.
    aiModel: 'local-model',
    // Phase 13.2 — the BUILT-IN base AI. true (default) = the System Core + AI
    // Assistant use the bundled Ollama model (no setup, runs on anything). false
    // = fall back to LM Studio if it's installed + running. "Start without AI"
    // at boot flips the master aiEnabled off so nothing loads at all.
    baseAiEnabled: true,
    crtFx: false,            // CRT scanline/flicker effect OFF by default (crisp + readable)
    glow: false,             // text glow OFF by default (no discoloration)
    // P1 — visual theme. 'green' = classic green-phosphor terminal (default,
    // unchanged for existing users). 'gold' = retro gold-on-gunmetal "sci-fi
    // fortress" look that matches Outlaw CodeMaker. Pure CSS-variable swap, so
    // it costs nothing at runtime and can be flipped anytime in Settings.
    theme: 'green',
    performanceMode: false,  // gaming CPU governor / gamemode hint
    updateRepo: 'Sup095/Outlaw-Game-OS',  // "owner/repo" the self-updater checks for releases (overridable in Settings)
    updateChannel: 'stable', // 'stable' = latest non-prerelease; 'beta' = newest release of any kind
    autoCheck: true,         // background check for shell updates
    lastUpdateCheck: 0,
    lastNotifiedVersion: '', // don't re-toast the same available version
    sponsorUrl: '',          // optional donate / sponsor URL (Ko-fi, BMC, GH Sponsors, etc.)
    firstRunDone: false,
    // Phase 6 — first-boot Quickstart tour. Shown once on the first desktop
    // entry; set true on Skip/Finish ("don't show again"). Replayable from Help.
    quickstartSeen: false,
    // SC5 — System Core voice. OFF by default. When ON, cold-mode dialogue
    // lines are routed through piper / espeak-ng for spoken playback. CPU-only,
    // no VRAM use; the shell still speaks via text bubble even when this is OFF.
    coreVoiceEnabled: false,
    // SC7 — Aggressive VRAM saver mode. `auto` (default) reads NVML each tick
    // and flips into emergency mode under thresholds. `off` keeps the tier at
    // `free` even when VRAM is low. `lean` / `minimal` force-pin the tier
    // regardless of probe. See vram-tier.js for thresholds + effects.
    vramSaverMode: 'auto',
    // Live-ISO welcome card. The Dashboard shows it on every boot of the
    // live system until the user clicks "Don't show again" — at which point
    // this flips true and persists. Installed systems never have /run/archiso
    // so the card is never shown there regardless.
    liveWelcomeDismissed: false,
    // Phase 3c — show the sign-in lock screen on shell startup (installed
    // systems). Off on the live demo automatically.
    lockEnabled: true,
};

function loadSettings() {
    try {
        const raw = fs.readFileSync(SETTINGS_PATH, 'utf8');
        return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
    } catch {
        return { ...DEFAULT_SETTINGS };
    }
}

// Phase 14h — mirror the chosen theme to a plain $HOME dotfile so the boot-time
// greeter (a SEPARATE Electron app that can't reach this app's userData) can
// match its palette to the desktop's. Best-effort and non-fatal: if it never
// lands, the greeter just falls back to the green default. Mirrors the existing
// ~/.outlaw-session* convention the greeter already reads.
function mirrorThemeToHome(theme) {
    try {
        const t = (typeof theme === 'string' && theme) ? theme : 'green';
        fs.writeFileSync(path.join(app.getPath('home'), '.outlaw-theme'), t + '\n', { mode: 0o600 });
    } catch { /* non-fatal — greeter falls back to green */ }
}

function saveSettings(s) {
    try {
        fs.mkdirSync(path.dirname(SETTINGS_PATH), { recursive: true });
        fs.writeFileSync(SETTINGS_PATH, JSON.stringify(s, null, 2));
    } catch (e) {
        console.error('Could not persist settings:', e.message);
    }
    mirrorThemeToHome(s && s.theme);
    return s;
}

// ---------------------------------------------------------------------------
// Phase 15b — persistent AI chats (Cr1tt3r). Named, multi-turn conversations
// stored in userData so they SURVIVE app updates (the app code in /usr/share is
// replaced on update; userData is not). The renderer owns the conversation
// logic; these helpers just load/save the whole (small) store as one JSON blob.
// ---------------------------------------------------------------------------
const AI_CHATS_PATH = path.join(app.getPath('userData'), 'ai-chats.json');

function loadAiChats() {
    try {
        const store = JSON.parse(fs.readFileSync(AI_CHATS_PATH, 'utf8'));
        if (store && Array.isArray(store.conversations)) return store;
    } catch { /* absent or corrupt — start fresh */ }
    return { activeId: null, conversations: [] };
}

function saveAiChats(store) {
    try {
        fs.mkdirSync(path.dirname(AI_CHATS_PATH), { recursive: true });
        const safe = (store && Array.isArray(store.conversations)) ? store : { activeId: null, conversations: [] };
        fs.writeFileSync(AI_CHATS_PATH, JSON.stringify(safe, null, 2));
        return true;
    } catch (e) {
        console.error('Could not persist AI chats:', e.message);
        return false;
    }
}

// ---------------------------------------------------------------------------
// Auth — 4-digit PIN (Outlaw-level convenience credential) + account password.
// The PIN is stored ONLY as a salted scrypt hash in a 0600 file (never plain
// text). The account password is verified against PAM via `sudo -S -v` (so the
// real OS password always works as a fallback). The PIN gates the sign-in
// screen and "important" installs; ordinary installs are passwordless via the
// 49-outlaw polkit rule.
// ---------------------------------------------------------------------------
const AUTH_FILE = path.join(app.getPath('userData'), 'auth.json');
const IS_LIVE = (() => { try { return process.platform === 'linux' && fs.existsSync('/run/archiso'); } catch { return false; } })();
let _authFails = 0;       // failed unlock attempts this session (rate-limit)
let _authLockUntil = 0;   // epoch ms; unlocking blocked until then

function readAuth() {
    try { return JSON.parse(fs.readFileSync(AUTH_FILE, 'utf8')); } catch { return null; }
}
function hasPin() { const a = readAuth(); return !!(a && a.pinHash && a.pinSalt); }
function setPin(pin) {
    if (!/^\d{4}$/.test(String(pin))) return false;
    const salt = crypto.randomBytes(16).toString('hex');
    const hash = crypto.scryptSync(String(pin), salt, 32).toString('hex');
    try {
        fs.mkdirSync(path.dirname(AUTH_FILE), { recursive: true });
        fs.writeFileSync(AUTH_FILE, JSON.stringify({ pinSalt: salt, pinHash: hash }), { mode: 0o600 });
        try { fs.chmodSync(AUTH_FILE, 0o600); } catch {}
        return true;
    } catch { return false; }
}
function clearPin() { try { fs.unlinkSync(AUTH_FILE); } catch {} return true; }
function verifyPin(pin) {
    const a = readAuth();
    if (!a || !a.pinHash || !a.pinSalt || !/^\d{4}$/.test(String(pin))) return false;
    try {
        const h = crypto.scryptSync(String(pin), a.pinSalt, 32).toString('hex');
        return crypto.timingSafeEqual(Buffer.from(h, 'hex'), Buffer.from(a.pinHash, 'hex'));
    } catch { return false; }
}
function verifyPassword(pw) {
    return new Promise((resolve) => {
        if (process.platform !== 'linux') return resolve(false);
        let p;
        try { p = spawn('sudo', ['-S', '-p', '', '-v'], { stdio: ['pipe', 'ignore', 'ignore'] }); }
        catch { return resolve(false); }
        p.on('error', () => resolve(false));
        p.on('close', (code) => { try { spawn('sudo', ['-k'], { stdio: 'ignore' }).unref(); } catch {} resolve(code === 0); });
        try { p.stdin.write(String(pw || '') + '\n'); p.stdin.end(); } catch { resolve(false); }
    });
}
// Unlock with either a PIN or the account password. Rate-limited.
async function authUnlock({ pin, password }) {
    const now = Date.now();
    if (now < _authLockUntil) {
        return { ok: false, error: 'Too many attempts — wait a few seconds.', waitMs: _authLockUntil - now };
    }
    let ok = false;
    if (pin != null && pin !== '') ok = verifyPin(pin);
    else if (password != null && password !== '') ok = await verifyPassword(password);
    if (ok) { _authFails = 0; return { ok: true }; }
    _authFails += 1;
    if (_authFails >= 5) { _authLockUntil = Date.now() + 8000; _authFails = 0; }
    return { ok: false, error: 'Incorrect — try again.' };
}

// ---------------------------------------------------------------------------
// Resilient package install/update. Prefer the installed /usr/local/bin helpers
// (passwordless via the 49-outlaw polkit rule). If a helper is MISSING — e.g.
// the shell was updated on a system installed before the helpers existed — fall
// back to the same logic written to a temp script and run via `pkexec bash`
// (works, but prompts since it isn't the allowlisted program). This turns the
// fatal "no such file or directory" into a working install.
// ---------------------------------------------------------------------------
const PKG_INSTALL_SH = [
    '#!/bin/bash',
    'set -uo pipefail',
    "if ! grep -qE '^\\[multilib\\]' /etc/pacman.conf; then sed -i '/^#\\[multilib\\]/{s/^#//; n; s/^#//}' /etc/pacman.conf; fi",
    'if [ ! -s /etc/pacman.d/gnupg/pubring.gpg ]; then pacman-key --init >/dev/null 2>&1 || true; pacman-key --populate archlinux >/dev/null 2>&1 || true; fi',
    'pacman -Syy --noconfirm || { echo "could not synchronize databases"; exit 4; }',
    'pacman -S --needed --noconfirm "$@"',
    '',
].join('\n');
const PKG_UPDATE_SH = '#!/bin/bash\nset -uo pipefail\npacman -Syu --noconfirm\n';

function tempScript(name, content) {
    const p = path.join(os.tmpdir(), name);
    try { fs.writeFileSync(p, content, { mode: 0o755 }); return p; } catch { return null; }
}
function privInstall(pkgList, timeout) {
    const helper = '/usr/local/bin/outlaw-pkg-install';
    if (IS_LINUX && fs.existsSync(helper)) return runShell(`pkexec ${helper} ${pkgList}`, { timeout });
    const tmp = tempScript('outlaw-pkg-install.sh', PKG_INSTALL_SH);
    if (!tmp) return Promise.resolve({ code: 1, stdout: '', stderr: 'Could not prepare the installer.' });
    return runShell(`pkexec bash ${tmp} ${pkgList}`, { timeout });
}
function privUpdate(timeout) {
    const helper = '/usr/local/bin/outlaw-update-pkgs';
    if (IS_LINUX && fs.existsSync(helper)) return runShell(`pkexec ${helper}`, { timeout });
    const tmp = tempScript('outlaw-update-pkgs.sh', PKG_UPDATE_SH);
    if (!tmp) return Promise.resolve({ code: 1, stdout: '', stderr: 'Could not prepare the updater.' });
    return runShell(`pkexec bash ${tmp}`, { timeout });
}

let settings = loadSettings();

// ---------------------------------------------------------------------------
// Allowlisted application launchers
// The renderer can only ask for an *id*; it can never name an arbitrary binary.
// ---------------------------------------------------------------------------
const APP_REGISTRY = {
    browser:   { label: 'Web Browser',  bin: 'opera',          args: [], fallbacks: ['opera-gx', 'firefox', 'chromium'] },
    steam:     { label: 'Steam',        bin: 'steam',          args: [] },
    lutris:    { label: 'Lutris',       bin: 'lutris',         args: [] },
    heroic:    { label: 'Heroic',       bin: 'heroic',         args: [] },
    godot:     { label: 'Godot',        bin: 'godot',          args: [] },
    blender:   { label: 'Blender',      bin: 'blender',        args: [] },
    gimp:      { label: 'GIMP',         bin: 'gimp',           args: [] },
    code:      { label: 'VS Code',      bin: 'code',           args: [], fallbacks: ['code-oss', 'codium'] },
    files:     { label: 'Files',        bin: 'thunar',         args: [], fallbacks: ['pcmanfm', 'nautilus'] },
    lmstudio:  { label: 'LM Studio',    bin: 'outlaw-lm-studio', args: [], fallbacks: ['lm-studio', 'lmstudio'] },
    wireshark: { label: 'Wireshark',    bin: 'wireshark',      args: [] },
    burp:      { label: 'Burp Suite',   bin: 'burpsuite',      args: [] },
    obs:       { label: 'OBS Studio',   bin: 'obs',            args: [] },
    terminal:  { label: 'Terminal',     bin: 'xfce4-terminal', args: [], fallbacks: ['xterm', 'alacritty'] },
};

// ---------------------------------------------------------------------------
// Apps catalog — the curated allowlist of optional, on-demand installs.
// The Apps screen in the renderer shows these; clicking Install runs
//   pkexec pacman -S --needed --noconfirm <pkg>
// All packages here are in official Arch repos (core/extra/community/multilib)
// so no AUR helper is required. The renderer can ONLY ask to install by `id`;
// it can never name an arbitrary package — the catalog IS the allowlist.
// ---------------------------------------------------------------------------
const APP_CATALOG = [
    // ----- Essentials (the first-boot bundles, also installable here any time
    // if you skipped them on first login). `extra` packages install alongside
    // the primary `pkg`; install-state is tracked on `pkg`. -----
    { id: 'steam',       pkg: 'steam',             category: 'Essentials',   label: 'Steam + gaming stack', description: 'Steam client plus GameMode, Gamescope, MangoHud and the Vulkan / 32-bit gaming libraries.', bin: 'steam',
      extra: ['gamemode', 'lib32-gamemode', 'gamescope', 'mangohud', 'lib32-mangohud', 'vulkan-icd-loader', 'lib32-vulkan-icd-loader', 'vulkan-tools', 'lib32-mesa'] },
    { id: 'firefox',     pkg: 'firefox',           category: 'Essentials',   label: 'Firefox',         description: 'The Firefox web browser.',                                        bin: 'firefox' },
    { id: 'godot',       pkg: 'godot',             category: 'Essentials',   label: 'Godot Engine',    description: 'The Godot game engine (GDScript) — what Outlaw CodeMaker builds games in.', bin: 'godot' },

    // ----- Game Dev -----
    { id: 'blender',     pkg: 'blender',           category: 'Game Dev',     label: 'Blender',         description: '3D modeling, rigging, animation, and sculpting.',                 bin: 'blender' },
    { id: 'gimp',        pkg: 'gimp',              category: 'Game Dev',     label: 'GIMP',            description: 'Raster image editor for sprites and textures.',                   bin: 'gimp' },
    { id: 'code',        pkg: 'code',              category: 'Game Dev',     label: 'VS Code',         description: 'Code editor with extensions. Pairs well with GDScript.',          bin: 'code' },
    { id: 'krita',       pkg: 'krita',             category: 'Game Dev',     label: 'Krita',           description: 'Digital painting for concept art and 2D animation.',              bin: 'krita' },
    { id: 'inkscape',    pkg: 'inkscape',          category: 'Game Dev',     label: 'Inkscape',        description: 'Vector editor for UI and SVG assets.',                            bin: 'inkscape' },
    { id: 'audacity',    pkg: 'audacity',          category: 'Game Dev',     label: 'Audacity',        description: 'Audio editor for SFX and music.',                                 bin: 'audacity' },
    { id: 'tiled',       pkg: 'tiled',             category: 'Game Dev',     label: 'Tiled',           description: 'Tilemap editor — great for 2D level design.',                     bin: 'tiled' },

    // ----- Gaming -----
    { id: 'lutris',      pkg: 'lutris',            category: 'Gaming',       label: 'Lutris',          description: 'Non-Steam game launcher (GOG, Epic, emulators).',                 bin: 'lutris' },
    { id: 'wine',        pkg: 'wine',              category: 'Gaming',       label: 'Wine',            description: 'Run Windows games and apps on Linux.' },
    { id: 'winetricks',  pkg: 'winetricks',        category: 'Gaming',       label: 'Winetricks',      description: 'Workarounds + components for Wine.' },
    { id: 'discord',     pkg: 'discord',           category: 'Gaming',       label: 'Discord',         description: 'Voice and text chat.',                                            bin: 'discord' },

    // ----- Browsers -----
    { id: 'chromium',    pkg: 'chromium',          category: 'Browsers',     label: 'Chromium',        description: 'Alternative to Firefox.',                                         bin: 'chromium' },

    // ----- Productivity / utilities -----
    { id: 'vim',         pkg: 'vim',               category: 'Productivity', label: 'Vim',             description: 'Modal terminal text editor.' },
    { id: 'vlc',         pkg: 'vlc',               category: 'Productivity', label: 'VLC',             description: 'Media player.',                                                   bin: 'vlc' },
    { id: 'libreoffice', pkg: 'libreoffice-fresh', category: 'Productivity', label: 'LibreOffice',     description: 'Documents, spreadsheets, presentations.',                         bin: 'libreoffice' },
    { id: 'obs',         pkg: 'obs-studio',        category: 'Productivity', label: 'OBS Studio',      description: 'Screen recording / streaming.',                                   bin: 'obs' },

    // ----- Security (authorized testing only) -----
    { id: 'nmap',        pkg: 'nmap',              category: 'Security',     label: 'Nmap',            description: 'Network scanning.' },
    { id: 'wireshark',   pkg: 'wireshark-qt',      category: 'Security',     label: 'Wireshark',       description: 'Packet capture and analysis.',                                    bin: 'wireshark' },
    { id: 'tcpdump',     pkg: 'tcpdump',           category: 'Security',     label: 'tcpdump',         description: 'CLI packet capture.' },
    { id: 'john',        pkg: 'john',              category: 'Security',     label: 'John the Ripper', description: 'Password cracker (CPU).' },
    { id: 'hashcat',     pkg: 'hashcat',           category: 'Security',     label: 'Hashcat',         description: 'Password cracker (GPU).' },
    { id: 'sqlmap',      pkg: 'sqlmap',            category: 'Security',     label: 'sqlmap',          description: 'SQL injection testing.' },
    { id: 'aircrack',    pkg: 'aircrack-ng',       category: 'Security',     label: 'Aircrack-ng',     description: 'Wireless network security testing.' },
    { id: 'hydra',       pkg: 'hydra',             category: 'Security',     label: 'Hydra',           description: 'Network login brute-force.' },
    { id: 'netcat',      pkg: 'gnu-netcat',        category: 'Security',     label: 'netcat',          description: 'Network swiss-army knife.' },
];

function which(bin) {
    return new Promise((resolve) => {
        if (!IS_LINUX) return resolve(null);
        execFile('command', ['-v', bin], { shell: '/bin/bash' }, (err, out) => {
            resolve(err ? null : (out || '').trim());
        });
    });
}

async function resolveBinary(entry) {
    const candidates = [entry.bin, ...(entry.fallbacks || [])];
    for (const c of candidates) {
        if (await which(c)) return c;
    }
    return null;
}

// Pin a frameless window to the full primary display and keep it there when the
// display resizes. Works without a window manager (where `fullscreen: true` is
// a no-op). Forcing fullscreen OFF first guarantees setBounds isn't ignored.
function fitToScreen(winRef) {
    const apply = () => {
        try {
            // workArea (not .size) so the window leaves room for the tint2
            // taskbar. Without a WM/taskbar, workArea === the full display, so
            // this still fills the screen. Re-fits when the taskbar appears
            // (its strut changes the work area → display-metrics-changed).
            const { x, y, width, height } = screen.getPrimaryDisplay().workArea;
            if (winRef.isFullScreen()) winRef.setFullScreen(false);
            // Idempotent: only move the window if it isn't already correct.
            // Calling setBounds needlessly (e.g. on a spurious display-metrics
            // event) can dismiss a just-opened native popup.
            const b = winRef.getBounds();
            if (b.x !== x || b.y !== y || b.width !== width || b.height !== height) {
                winRef.setBounds({ x, y, width, height });
            }
        } catch { /* window may be gone */ }
    };
    winRef.once('ready-to-show', () => {
        apply();
        // The taskbar (tint2) may reserve its strut a moment after the shell
        // maps, shrinking the work area; re-fit once it settles. Belt-and-braces
        // alongside the display-metrics-changed listener.
        setTimeout(apply, 1500);
    });
    const onChange = () => apply();
    screen.on('display-metrics-changed', onChange);
    winRef.on('closed', () => { try { screen.removeListener('display-metrics-changed', onChange); } catch {} });
}

function launchDetached(bin, args = [], opts = {}) {
    const child = spawn(bin, args, { detached: true, stdio: 'ignore' });
    child.on('error', (e) => console.error(`launch ${bin} failed:`, e.message));
    child.unref();
    // No-WM focus fix: the Outlaw session runs without a window manager, so a
    // newly-launched window appears on top but never receives X keyboard focus
    // — you can see it but can't type into it. outlaw-focus sets input focus
    // directly (xdotool) once the window maps. `opts.focus` is a window
    // name/class substring; it defaults to the binary's basename (matches most
    // apps' WM_CLASS). Pass `focus: false` for launchers that focus themselves
    // (e.g. outlaw-term). Best-effort, Linux-only, never throws.
    if (IS_LINUX && opts.focus !== false) {
        const pat = (typeof opts.focus === 'string' && opts.focus) || String(bin).split('/').pop();
        try { spawn('outlaw-focus', [pat], { detached: true, stdio: 'ignore' }).unref(); } catch {}
    }
    return child;
}

// --- App auto-discovery (Phase 2) ------------------------------------------
// Surface apps the user installed themselves — freedesktop `.desktop` entries
// and AppImages dropped into common folders — so they appear in the Apps page
// next to the curated catalog and launch in one click. Pure filesystem reads,
// Linux-only, and tolerant of missing dirs / unreadable files (never throws).
function _parseDesktopEntry(txt) {
    const e = {};
    let inMain = false;
    for (const raw of String(txt).split('\n')) {
        const line = raw.trim();
        if (line.startsWith('[')) { inMain = (line === '[Desktop Entry]'); continue; }
        if (!inMain || !line || line.startsWith('#')) continue;
        const i = line.indexOf('=');
        if (i < 0) continue;
        const k = line.slice(0, i).trim();
        const v = line.slice(i + 1).trim();
        if (k === 'Name' && !e.name) e.name = v;            // locale-less Name= wins
        else if (k === 'Exec' && !e.exec) e.exec = v;
        else if (k === 'NoDisplay') e.noDisplay = /true/i.test(v);
        else if (k === 'Hidden') e.hidden = /true/i.test(v);
        else if (k === 'Type') e.type = v;
        else if (k === 'Terminal') e.terminal = /true/i.test(v);
    }
    return e;
}
function _cleanExec(exec) {
    // Strip desktop-entry field codes (%f %F %u %U %i %c %k %d %n %v %m …).
    return String(exec).replace(/%[a-zA-Z]/g, '').replace(/\s+/g, ' ').trim();
}
function _splitExec(s) {
    const out = []; let cur = ''; let q = null;
    for (const ch of s) {
        if (q) { if (ch === q) q = null; else cur += ch; }
        else if (ch === '"' || ch === "'") q = ch;
        else if (ch === ' ') { if (cur) { out.push(cur); cur = ''; } }
        else cur += ch;
    }
    if (cur) out.push(cur);
    return out;
}
function discoverApps() {
    if (!IS_LINUX) return [];
    const home = os.homedir();
    const desktopDirs = [
        '/usr/share/applications',
        '/usr/local/share/applications',
        path.join(home, '.local/share/applications'),
        '/var/lib/flatpak/exports/share/applications',
        path.join(home, '.local/share/flatpak/exports/share/applications'),
        '/var/lib/snapd/desktop/applications',
    ];
    const appImageDirs = [
        path.join(home, 'Downloads'),
        path.join(home, 'Applications'),
        path.join(home, 'Desktop'),
        path.join(home, 'AppImages'),
        home,
    ];
    const out = [];
    const seen = new Set();
    for (const dir of desktopDirs) {
        let files;
        try { files = fs.readdirSync(dir); } catch { continue; }
        for (const f of files) {
            if (!f.endsWith('.desktop')) continue;
            const full = path.join(dir, f);
            let txt;
            try { txt = fs.readFileSync(full, 'utf8'); } catch { continue; }
            const e = _parseDesktopEntry(txt);
            if (!e.name || !e.exec) continue;
            if (e.noDisplay || e.hidden) continue;
            if (e.type && e.type !== 'Application') continue;
            const key = e.name.toLowerCase();
            if (seen.has(key)) continue;
            seen.add(key);
            out.push({ id: 'd:' + full, name: e.name, exec: e.exec, kind: 'desktop', path: full, terminal: !!e.terminal });
        }
    }
    for (const dir of appImageDirs) {
        let files;
        try { files = fs.readdirSync(dir); } catch { continue; }
        for (const f of files) {
            if (!/\.appimage$/i.test(f)) continue;
            const full = path.join(dir, f);
            const key = 'ai:' + full.toLowerCase();
            if (seen.has(key)) continue;
            seen.add(key);
            out.push({ id: 'a:' + full, name: f.replace(/\.appimage$/i, ''), exec: full, kind: 'appimage', path: full });
        }
    }
    out.sort((a, b) => a.name.localeCompare(b.name));
    return out;
}

// ---------------------------------------------------------------------------
// Destructive command guard — the "don't accidentally nuke the PC" layer
// ---------------------------------------------------------------------------
const DANGER_PATTERNS = [
    { re: /\brm\s+(-[a-z]*\s+)*-[a-z]*[rf]/i, reason: 'Recursive/forced delete (rm -rf).' },
    { re: /\bdd\b[^|;]*\bof=\/dev\//i, reason: 'Raw write to a block device (dd of=/dev/...).' },
    { re: /\bmkfs(\.\w+)?\b/i, reason: 'Filesystem format (mkfs).' },
    { re: /\bwipefs\b/i, reason: 'Filesystem signature wipe (wipefs).' },
    { re: /\b(shred|blkdiscard)\b[^|;]*\/dev\//i, reason: 'Destroying data on a device.' },
    { re: /\b(fdisk|sfdisk|sgdisk|cfdisk|parted)\b/i, reason: 'Partition table editing.' },
    { re: />\s*\/dev\/(sd|nvme|vd|mmcblk)/i, reason: 'Redirecting output onto a disk device.' },
    { re: /\b(parted|sgdisk)\b[^|;]*--?(mklabel|zap-all|delete)/i, reason: 'Erasing partition layout.' },
    { re: /:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:/, reason: 'Fork bomb.' },
    { re: /\bchmod\s+-R\s+0*\s+\//i, reason: 'Recursive chmod from filesystem root.' },
    { re: /\bchown\s+-R\b[^|;]*\s\/(?:\s|$)/i, reason: 'Recursive chown of the filesystem root.' },
    { re: /\bmv\b[^|;]*\s\/dev\/null\b/i, reason: 'Moving data into /dev/null (destroys it).' },
    { re: /\b(pacman|yay)\b[^|;]*-R[a-z]*s[a-z]*\b/i, reason: 'Mass package removal with dependencies.' },
    { re: /\bgit\b[^|;]*\b(reset\s+--hard|clean\s+-[a-z]*f)/i, reason: 'Destructive git operation (discards work).' },
];

function classifyCommand(command) {
    const cmd = String(command || '');
    for (const p of DANGER_PATTERNS) {
        if (p.re.test(cmd)) return { danger: true, reason: p.reason };
    }
    return { danger: false, reason: '' };
}

function runShell(command, { timeout = 30000 } = {}) {
    return new Promise((resolve) => {
        if (!IS_LINUX) {
            return resolve({ code: 127, stdout: '', stderr: 'Shell commands only run on Outlaw OS (Linux).' });
        }
        const child = spawn('bash', ['-c', command], {
            cwd: os.homedir(),
            env: process.env,
        });
        trackedProcs.add(child);
        let stdout = '', stderr = '';
        const killer = setTimeout(() => child.kill('SIGKILL'), timeout);
        child.stdout.on('data', (d) => { stdout += d; });
        child.stderr.on('data', (d) => { stderr += d; });
        const cleanup = () => { clearTimeout(killer); trackedProcs.delete(child); };
        child.on('close', (code) => { cleanup(); resolve({ code, stdout: stdout.trim(), stderr: stderr.trim() }); });
        child.on('error', (err) => { cleanup(); resolve({ code: 1, stdout: '', stderr: err.message }); });
    });
}

// Kill every tracked subprocess. SIGTERM first; if anything is still alive
// after 1s, SIGKILL. Returns the number we acted on.
function killAllTrackedProcs() {
    const procs = Array.from(trackedProcs);
    for (const p of procs) {
        try { p.kill('SIGTERM'); } catch { /* already dead */ }
    }
    setTimeout(() => {
        for (const p of procs) {
            try {
                if (!p.killed) p.kill('SIGKILL');
            } catch { /* already dead */ }
        }
    }, 1000);
    return procs.length;
}

// ---------------------------------------------------------------------------
// System information helpers (read /proc; fall back gracefully off-Linux)
// ---------------------------------------------------------------------------
let lastCpu = null;
function readCpuSample() {
    try {
        const line = fs.readFileSync('/proc/stat', 'utf8').split('\n')[0];
        const p = line.trim().split(/\s+/).slice(1).map(Number);
        const idle = p[3] + (p[4] || 0);
        const total = p.reduce((a, b) => a + b, 0);
        return { idle, total };
    } catch {
        return null;
    }
}

function cpuPercent() {
    const sample = readCpuSample();
    if (!sample) {
        // cross-platform fallback via loadavg
        const load = os.loadavg()[0];
        const cores = os.cpus().length || 1;
        return Math.min(100, (load / cores) * 100);
    }
    if (!lastCpu) { lastCpu = sample; return 0; }
    const dIdle = sample.idle - lastCpu.idle;
    const dTotal = sample.total - lastCpu.total;
    lastCpu = sample;
    if (dTotal <= 0) return 0;
    return Math.max(0, Math.min(100, (1 - dIdle / dTotal) * 100));
}

function memInfo() {
    try {
        const txt = fs.readFileSync('/proc/meminfo', 'utf8');
        const get = (k) => Number((txt.match(new RegExp(`${k}:\\s+(\\d+)`)) || [])[1] || 0);
        const total = get('MemTotal'), avail = get('MemAvailable');
        return { totalKb: total, usedKb: total - avail };
    } catch {
        return { totalKb: Math.round(os.totalmem() / 1024), usedKb: Math.round((os.totalmem() - os.freemem()) / 1024) };
    }
}

function fmtGb(kb) { return (kb / 1024 / 1024).toFixed(1) + 'G'; }

// ---- Phase 4: local-AI model recommendation -------------------------------
// Given total system RAM and discrete-GPU VRAM (GB), pick a model the machine
// can realistically run in LM Studio, plus suggested settings. We always also
// return a "starter" model that runs on practically any PC — once it's loaded
// it can guide the user through the rest of the setup itself.
// opts (Phase 14d): { purpose:'desktop'|'dev', tier:'powerful'|'minimal' (desktop),
// spill:bool (dev — spill the model into system RAM beyond VRAM) }. Defaults
// (no opts) = desktop/powerful, identical to the original behaviour so existing
// callers (gatherSpecs, machineSummary) are unaffected.
function recommendModel(ramGb, vramGb, opts = {}) {
    const purpose = opts.purpose === 'dev' ? 'dev' : 'desktop';
    const tier = opts.tier === 'minimal' ? 'minimal' : 'powerful';
    const spill = !!opts.spill;
    const gpu = vramGb >= 4;                       // a usable discrete GPU?
    const starter = { model: 'Qwen2.5 0.5B Instruct (Q4_K_M)', size: '~0.4 GB',
                      note: 'Runs on almost anything, even old laptops.' };
    // General instruct catalogue (desktop), smallest → largest.
    const M = {
        s05: { model: 'Qwen2.5 0.5B Instruct (Q4_K_M)', size: '~0.4 GB', ctx: 2048, tier: 'tiny' },
        s3:  { model: 'Llama 3.2 3B Instruct (Q4_K_M)',  size: '~2.2 GB', ctx: 4096, tier: 'small' },
        s7:  { model: 'Qwen2.5 7B Instruct (Q4_K_M)',    size: '~4.7 GB', ctx: 8192, tier: 'medium' },
        s14: { model: 'Qwen2.5 14B Instruct (Q4_K_M)',   size: '~9 GB',   ctx: 8192, tier: 'large' },
        s32: { model: 'Qwen2.5 32B Instruct (Q4_K_M)',   size: '~19 GB',  ctx: 8192, tier: 'xl' },
    };
    // Coding catalogue (Dev session) — Qwen2.5-Coder, bigger context for code.
    const C = {
        c15: { model: 'Qwen2.5-Coder 1.5B (Q4_K_M)', size: '~1.0 GB', ctx: 8192,  tier: 'tiny' },
        c7:  { model: 'Qwen2.5-Coder 7B (Q4_K_M)',   size: '~4.7 GB', ctx: 16384, tier: 'medium' },
        c14: { model: 'Qwen2.5-Coder 14B (Q4_K_M)',  size: '~9 GB',   ctx: 16384, tier: 'large' },
        c32: { model: 'Qwen2.5-Coder 32B (Q4_K_M)',  size: '~19 GB',  ctx: 16384, tier: 'xl' },
    };
    let rec, budget, runsOn, note;

    if (purpose === 'dev') {
        // Best CODING model the machine can run. Optional spill borrows spare RAM
        // beyond VRAM for a bigger (slower) model.
        if (gpu) {
            budget = spill ? vramGb + Math.max(0, ramGb - 4) * 0.5 : vramGb;
            runsOn = spill
                ? `GPU + RAM spill (${vramGb} GB VRAM + system RAM — larger model, a little slower)`
                : `GPU only (${vramGb} GB VRAM — fastest)`;
        } else {
            budget = Math.max(1, ramGb - 4);
            runsOn = 'CPU + RAM (no discrete GPU — slower; a smaller coder model stays usable)';
        }
        rec = budget < 6 ? C.c15 : budget < 11 ? C.c7 : budget < 20 ? C.c14 : C.c32;
        note = 'Coding model for the Dev session (Outlaw CodeMaker).';
    } else if (tier === 'minimal') {
        // Desktop, minimal-but-useful — small + capable, for system control and
        // the built-in AI's job done better. Deliberately light on resources.
        budget = gpu ? vramGb : Math.max(1, ramGb - 4);
        rec = budget >= 6 ? M.s7 : M.s3;
        runsOn = gpu ? `GPU offload (${vramGb} GB VRAM — light)` : 'CPU + RAM (light footprint)';
        note = "Lean desktop assistant — system control + the built-in AI's job, but better.";
    } else {
        // Desktop, most-powerful — the biggest general model the PC can run.
        if (gpu) { budget = vramGb; rec = vramGb < 6 ? M.s3 : vramGb < 11 ? M.s7 : vramGb < 20 ? M.s14 : M.s32; }
        else { budget = Math.max(1, ramGb - 4); rec = ramGb < 6 ? M.s05 : ramGb < 12 ? M.s3 : ramGb < 24 ? M.s7 : M.s14; }
        runsOn = gpu ? `GPU offload (${vramGb} GB VRAM — fast)`
                     : 'CPU + RAM (works everywhere, but slower — drop to a smaller model if it lags)';
        note = 'The most capable general model your PC can run.';
    }

    return {
        gpu, purpose, budgetGb: Math.round(budget * 10) / 10,
        tier: purpose === 'desktop' ? tier : null,
        spill: purpose === 'dev' ? spill : null,
        runsOn, note, gpuOffload: gpu,
        starter, recommended: rec,
        sameAsStarter: rec.model === starter.model,
    };
}

// Read this PC's specs (RAM / discrete-GPU VRAM / CPU) once and cache them —
// hardware doesn't change during a session, and the nvidia-smi probe is the
// only slow bit. Shared by the AI setup card, the spec-aware prompts, and the
// setup chat so they all agree.
let _specsCache = null;
async function gatherSpecs() {
    if (_specsCache) return _specsCache;
    const mem = memInfo();
    const ramGb = Math.round((mem.totalKb / 1024 / 1024) * 10) / 10;
    let vramGb = 0, gpuName = '';
    if (IS_LINUX) {
        const nv = await runShell(
            'nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | head -n 1',
            { timeout: 3000 });
        if (nv.code === 0 && nv.stdout) {
            const p = nv.stdout.split(',').map((s) => s.trim());
            gpuName = p[0] || 'NVIDIA GPU';
            vramGb = Math.round((Number(p[1]) || 0) / 1024 * 10) / 10;
        } else {
            const lspci = await runShell(
                "lspci 2>/dev/null | grep -Ei 'vga|3d|display' | sed 's/^.*: //' | head -n 1",
                { timeout: 2000 });
            gpuName = (lspci.stdout || '').trim();
        }
    }
    const cores = os.cpus().length;
    const cpu = (os.cpus()[0] || {}).model || 'CPU';
    _specsCache = { ramGb, vramGb, gpuName, cores, cpu, ...recommendModel(ramGb, vramGb) };
    return _specsCache;
}

// Compact one-liner used to make the local AI hardware-aware in its prompt.
function machineSummary(s) {
    const gpu = s.vramGb > 0
        ? `${s.gpuName || 'GPU'} with ${s.vramGb}GB VRAM`
        : (s.gpuName ? `${s.gpuName} (no dedicated VRAM)` : 'no discrete GPU');
    return `${s.cpu}, ${s.cores} cores, ${s.ramGb}GB RAM, ${gpu}. `
        + `Best local model for it: ${s.recommended.model} (${s.recommended.size}), `
        + `context ${s.recommended.ctx}, GPU offload ${s.gpuOffload ? 'on' : 'off'}. `
        + `Starter model that runs on anything: ${s.starter.model}.`;
}

// Phase 13.2 — which local AI backend the desktop uses right now. Default = the
// built-in bundled Ollama model; when the user turns the built-in AI off, fall
// back to LM Studio (status() will report it unavailable if it isn't running).
function aiBackend() {
    if (settings.baseAiEnabled !== false) {
        return { baseUrl: OLLAMA_V1, model: BASE_AI_MODEL, kind: 'base' };
    }
    return { baseUrl: LM_STUDIO_V1, model: settings.aiModel || 'local-model', kind: 'lmstudio' };
}

// Pull the bundled base model if it isn't present yet (first desktop run). Runs
// only when the built-in AI is on; streams to the loading screen. Ollama must be
// installed + its service running (the installer enables it).
async function ensureBaseModel() {
    if (!IS_LINUX) return { ok: false, reason: 'not-linux' };
    if (settings.baseAiEnabled === false) return { ok: false, reason: 'base-ai-off' };
    const have = await runShell(`ollama list 2>/dev/null | grep -F "${BASE_AI_MODEL}"`, { timeout: 8000 });
    if (have.code === 0 && have.stdout.trim()) return { ok: true, present: true };
    // Not present — pull it (streamed to the loading screen).
    const labels = ['Preparing', 'Downloading model', 'Verifying', 'Finishing'];
    const matchers = [null, /pulling|downloading|manifest/i, /verifying|writing/i, /success/i];
    const r = await runStreamingJob('ollama', ['pull', BASE_AI_MODEL], labels, matchers);
    return { ok: r.ok, pulled: r.ok };
}

async function systemInfo() {
    const mem = memInfo();
    let kernel = os.release();
    let cpuModel = (os.cpus()[0] || {}).model || 'Unknown CPU';
    if (IS_LINUX) {
        const k = await runShell('uname -r'); if (k.code === 0) kernel = k.stdout;
        const c = await runShell("LC_ALL=C lscpu | sed -n 's/^Model name:[[:space:]]*//p'");
        if (c.code === 0 && c.stdout) cpuModel = c.stdout.split('\n')[0];
    }
    return {
        hostname: os.hostname(),
        kernel,
        cpu: cpuModel,
        cores: os.cpus().length,
        ramTotal: fmtGb(mem.totalKb),
        ramUsed: fmtGb(mem.usedKb),
        uptime: Math.round(os.uptime()),
        platform: process.platform,
        appVersion: APP_VERSION,
    };
}

function sendToast(msg) {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('toast', msg);
    }
}

// ---------------------------------------------------------------------------
// AI orchestration: parse intent -> map to a safe action or a confirm request
// ---------------------------------------------------------------------------
// Phase 13: resolve an app name the AI was asked to install to a KNOWN source
// only — the curated Apps catalog first, then exact official-repo package names.
// Returns null for anything not in a known source (no arbitrary web downloads).
async function resolveInstallable(name) {
    const q = String(name || '').toLowerCase().trim().replace(/^install\s+/, '');
    if (!q) return null;
    const hit = APP_CATALOG.find((a) =>
        a.id === q || a.pkg === q || (a.label || '').toLowerCase() === q
        || a.id.includes(q) || (a.label || '').toLowerCase().includes(q));
    if (hit) return { pkg: hit.pkg, extra: hit.extra || [], label: hit.label, source: 'the Apps catalog' };
    // Official repos — EXACT package name first (validated, no shell metacharacters).
    if (IS_LINUX && /^[a-z0-9][a-z0-9._+-]*$/.test(q)) {
        const r = await runShell(`pacman -Si ${q}`, { timeout: 8000 });
        if (r.code === 0) return { pkg: q, extra: [], label: q, source: 'the official repositories' };
    }
    // Phase 15c — fuzzy fallback: search the repos for the best match so a DESCRIBED
    // need ("something to edit audio") or a slightly-off name still resolves to a
    // real, installable package. The user still confirms before anything installs.
    if (IS_LINUX && /^[a-z0-9][a-z0-9 ._+-]{0,39}$/i.test(q)) {
        const terms = q.split(/\s+/).filter(Boolean).map((w) => `'${w}'`).join(' ');
        const r = await runShell(`pacman -Ss ${terms}`, { timeout: 12000 });
        const line = (r.stdout || '').split('\n').find((l) => /^\w[\w-]*\/\S+\s+/.test(l));
        const m = line && line.match(/^\w[\w-]*\/(\S+)\s+/);
        if (m) return { pkg: m[1], extra: [], label: m[1], source: 'the official repositories', fuzzy: true };
    }
    return null;
}

async function executeIntent(intent) {
    switch (intent.tool) {
        case 'system_info': {
            const i = await systemInfo();
            return { text: `${i.hostname} • ${i.cpu} (${i.cores} cores) • RAM ${i.ramUsed}/${i.ramTotal} • kernel ${i.kernel}`, did: 'system_info' };
        }
        case 'open_app': {
            const id = (intent.arg || '').toLowerCase().trim();
            const entry = APP_REGISTRY[id];
            if (!entry) return { text: `I don't have an app called "${intent.arg}". Try the launcher buttons.`, did: 'none' };
            const bin = await resolveBinary(entry);
            if (!bin) return { text: `${entry.label} isn't installed.`, did: 'none' };
            launchDetached(bin, entry.args);
            return { text: `Opening ${entry.label}.`, did: 'open_app' };
        }
        case 'search_web': {
            const q = encodeURIComponent(intent.arg || '');
            const url = `https://duckduckgo.com/?q=${q}`;
            const entry = APP_REGISTRY.browser;
            const bin = await resolveBinary(entry);
            if (bin) launchDetached(bin, [url]); else shell.openExternal(url);
            return { text: `Searching the web for "${intent.arg}".`, did: 'search_web' };
        }
        case 'list_files': {
            const dir = intent.arg && intent.arg.trim() ? intent.arg.trim() : os.homedir();
            const listing = await listFiles(dir);
            if (listing.error) return { text: listing.error, did: 'none' };
            const names = listing.entries.slice(0, 40).map((e) => (e.type === 'dir' ? e.name + '/' : e.name)).join('  ');
            return { text: `${listing.path}:\n${names || '(empty)'}`, did: 'list_files' };
        }
        case 'open_file': {
            const r = await openPath(intent.arg || '');
            return { text: r.ok ? `Opened ${intent.arg}.` : r.error, did: r.ok ? 'open_file' : 'none' };
        }
        case 'install_app': {
            // Phase 13: only ever install from a KNOWN source, and only after the
            // user confirms. Hand a proposal back to the UI (same rail as run_command).
            const resolved = await resolveInstallable(intent.arg || '');
            if (!resolved) {
                return { text: `I can only install from known sources (the Apps catalog or the official repositories), and I couldn't find "${intent.arg}" there. You can browse the Apps page instead.`, did: 'none' };
            }
            const proposal = resolved.fuzzy
                ? `Closest match I found is "${resolved.label}" in ${resolved.source}. Confirm to install it (or browse the Apps page for more).`
                : (intent.text || `I can install ${resolved.label} from ${resolved.source}. Confirm to proceed.`);
            return {
                needsConfirm: true,
                action: { tool: 'install_app', pkg: resolved.pkg, extra: resolved.extra || [], label: resolved.label, source: resolved.source },
                text: proposal,
            };
        }
        case 'run_command':
            // Never auto-run. Hand back to UI for explicit confirmation.
            return {
                needsConfirm: true,
                action: { tool: 'run_command', arg: intent.arg || '' },
                classify: classifyCommand(intent.arg || ''),
                text: intent.text || 'I can run this command if you confirm.',
            };
        case 'answer':
        default:
            return { text: intent.text || '...', did: 'answer' };
    }
}

// ---------------------------------------------------------------------------
// File helpers (read-only listing + safe open)
// ---------------------------------------------------------------------------
async function listFiles(dir) {
    try {
        const real = path.resolve(dir);
        const entries = fs.readdirSync(real, { withFileTypes: true })
            .filter((d) => !d.name.startsWith('.'))
            .map((d) => {
                let size = 0;
                try { size = d.isFile() ? fs.statSync(path.join(real, d.name)).size : 0; } catch {}
                return { name: d.name, type: d.isDirectory() ? 'dir' : 'file', size };
            })
            .sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1));
        return { path: real, parent: path.dirname(real), entries };
    } catch (e) {
        return { path: dir, entries: [], error: `Cannot open ${dir}: ${e.code || e.message}` };
    }
}

async function openPath(target) {
    try {
        const real = path.resolve(target);
        if (!fs.existsSync(real)) return { ok: false, error: `Not found: ${target}` };
        const err = await shell.openPath(real); // never goes through a shell -> no injection
        return err ? { ok: false, error: err } : { ok: true };
    } catch (e) {
        return { ok: false, error: e.message };
    }
}

// ---- Phase 5: process control (End task / End process tree) ----------------
function toPid(x) { const n = parseInt(x, 10); return Number.isInteger(n) && n > 1 ? n : 0; }

// All descendants of `root` (inclusive), leaves-first, so children die before
// their parents. Single `ps` call; pure parse — no shell expansion of the pid.
async function descendantPids(root) {
    if (!IS_LINUX) return [root];
    const r = await runShell('ps -eo pid=,ppid=');
    const kids = new Map();
    for (const line of (r.stdout || '').split('\n')) {
        const m = line.trim().match(/^(\d+)\s+(\d+)$/);
        if (!m) continue;
        const pid = parseInt(m[1], 10), ppid = parseInt(m[2], 10);
        if (!kids.has(ppid)) kids.set(ppid, []);
        kids.get(ppid).push(pid);
    }
    const order = [];
    const seen = new Set();
    (function walk(p) {
        if (seen.has(p)) return;          // guard against any ppid cycle
        seen.add(p);
        for (const c of (kids.get(p) || [])) walk(c);
        order.push(p);                    // post-order = leaves first
    })(root);
    return order;
}

function killPids(pids, signal) {
    let killed = 0; const errors = [];
    for (const pid of pids) {
        if (!pid || pid <= 1) continue;   // never SIGKILL init
        try { process.kill(pid, signal); killed++; }
        catch (e) {
            if (e.code === 'ESRCH') continue;                 // already gone
            errors.push(e.code === 'EPERM' ? `${pid}: needs admin` : `${pid}: ${e.code || e.message}`);
        }
    }
    return { ok: errors.length === 0, killed, errors };
}

// ---- Phase 12: streamed long-job runner (drives the loading screen) --------
// Spawns a command and streams its output to the renderer's loading screen via
// 'job-progress' events: { phases:[labels] } once at the start, { phase:i } as
// recognised markers go by, { log:line } per output line, { done, ok } at exit.
// Resolves { ok, code } so the caller still gets a final verdict.
function runStreamingJob(cmd, args, phaseLabels, phaseMatchers) {
    return new Promise((resolve) => {
        const send = (p) => {
            try { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('job-progress', p); }
            catch { /* window gone */ }
        };
        send({ phases: phaseLabels, phase: 0 });
        let phase = 0, child;
        try { child = spawn(cmd, args); }
        catch (e) { send({ log: 'error: ' + e.message, done: true, ok: false }); return resolve({ ok: false, error: e.message }); }
        const onData = (buf) => {
            for (const raw of String(buf).split('\n')) {
                const line = raw.replace(/\s+$/, '');
                if (!line) continue;
                for (let i = phase + 1; i < phaseMatchers.length; i++) {
                    if (phaseMatchers[i] && phaseMatchers[i].test(line)) { phase = i; send({ phase }); break; }
                }
                send({ log: line.slice(0, 200) });
            }
        };
        child.stdout && child.stdout.on('data', onData);
        child.stderr && child.stderr.on('data', onData);
        child.on('error', (e) => { send({ log: 'error: ' + e.message, done: true, ok: false }); resolve({ ok: false, error: e.message }); });
        child.on('close', (code) => { send({ phase: phaseLabels.length - 1, done: true, ok: code === 0 }); resolve({ ok: code === 0, code }); });
    });
}

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------
function registerIpc() {
    ipcMain.handle('system:info', () => systemInfo());

    // Phase 8: real boot messages for the cinematic boot screen. journalctl
    // -o cat = message text only (no timestamps); falls back to dmesg. Read-only
    // and unprivileged — returns [] if neither is readable, and the boot screen
    // just shows its synthetic lines instead.
    ipcMain.handle('system:boot-log', async () => {
        if (!IS_LINUX) return [];
        let r = await runShell('journalctl -b --no-pager -o cat -n 14 2>/dev/null');
        let out = (r.stdout || '').trim();
        if (!out) { r = await runShell('dmesg 2>/dev/null | tail -n 14'); out = (r.stdout || '').trim(); }
        return out ? out.split('\n').map((l) => l.replace(/\s+$/, '').slice(0, 92)).filter(Boolean) : [];
    });

    ipcMain.handle('system:stats', () => {
        const mem = memInfo();
        return { cpu: cpuPercent(), ramPct: mem.totalKb ? (mem.usedKb / mem.totalKb) * 100 : 0,
                 ramUsed: fmtGb(mem.usedKb), ramTotal: fmtGb(mem.totalKb), time: new Date().toLocaleTimeString() };
    });

    ipcMain.handle('system:processes', async () => {
        if (!IS_LINUX) return [{ pid: process.pid, comm: 'electron', cpu: '0.0', mem: '0.0', memMb: 0 }];
        // rss (KB) gives a Windows-style MB column. Return ALL processes (sorted
        // by CPU) so the user can filter/scroll to find any app to end — capped
        // at 250 so the render stays cheap on busy systems.
        const r = await runShell('ps -eo pid,comm,pcpu,pmem,rss --sort=-pcpu');
        return r.stdout.split('\n').slice(1).map((l) => {
            const m = l.trim().match(/^(\d+)\s+(.+?)\s+([\d.]+)\s+([\d.]+)\s+(\d+)$/);
            return m ? { pid: m[1], comm: m[2], cpu: m[3], mem: m[4], memMb: Math.round(Number(m[5]) / 1024) } : null;
        }).filter(Boolean).slice(0, 250);
    });

    // Phase 5: End task / End process tree. We kill via Node's process.kill so
    // there's no shell + no injection surface. Only the user's own processes can
    // be ended without privilege (root-owned ones return EPERM, surfaced clearly
    // — same as Windows' "Access denied"). PID 1 is never touched.
    ipcMain.handle('proc:kill', async (_e, pid) => killPids([toPid(pid)], 'SIGTERM'));
    ipcMain.handle('proc:kill-tree', async (_e, pid) => {
        const root = toPid(pid);
        if (!root) return { ok: false, killed: 0, errors: ['invalid pid'] };
        // Forceful, leaves-first — mirrors Windows "End process tree".
        return killPids(await descendantPids(root), 'SIGKILL');
    });

    ipcMain.handle('system:gpu', async () => {
        if (!IS_LINUX) return 'GPU detection runs on Outlaw OS.';
        const r = await runShell("lspci 2>/dev/null | grep -Ei 'vga|3d|display' | sed 's/^.*: //' | head -n 2");
        return r.stdout || 'No discrete GPU detected.';
    });

    // ----- System Core detailed handlers (SC2) ------------------------------
    // All of these degrade gracefully off-Linux so the System Core screen
    // still renders during off-OS development (just with "—" values).

    ipcMain.handle('system:gpu-detailed', async () => {
        if (!IS_LINUX) {
            return { available: false, name: '', vramUsedMb: 0, vramTotalMb: 0,
                     vramPct: 0, source: 'preview', note: 'GPU probe runs on Outlaw OS.' };
        }
        // Prefer nvidia-smi for VRAM numbers; this is the only practical way
        // to read actual used VRAM from a shell call. CSV no-units keeps the
        // parser tiny. ~50ms call when present, ~3ms exit when absent.
        const nv = await runShell(
            'nvidia-smi --query-gpu=name,memory.used,memory.total ' +
            '--format=csv,noheader,nounits 2>/dev/null | head -n 1',
            { timeout: 3000 },
        );
        if (nv.code === 0 && nv.stdout) {
            const parts = nv.stdout.split(',').map((s) => s.trim());
            const name = parts[0] || 'NVIDIA GPU';
            const used = Number(parts[1]) || 0;
            const total = Number(parts[2]) || 0;
            const pct = total > 0 ? Math.round((used / total) * 100) : 0;
            return { available: true, name, vramUsedMb: used, vramTotalMb: total,
                     vramPct: pct, source: 'nvml' };
        }
        // Non-NVIDIA fallback — return the GPU model from lspci so the slot
        // isn't empty. VRAM stays at 0 (we can't read it generically).
        const lspci = await runShell(
            "lspci 2>/dev/null | grep -Ei 'vga|3d|display' | sed 's/^.*: //' | head -n 1",
            { timeout: 2000 },
        );
        const name = (lspci.stdout || '').trim() || 'GPU n/a';
        return { available: true, name, vramUsedMb: 0, vramTotalMb: 0,
                 vramPct: 0, source: 'lspci' };
    });

    ipcMain.handle('system:disk', async () => {
        if (!IS_LINUX) {
            return { mount: '/', usedMb: 0, totalMb: 0, pct: 0, available: false };
        }
        // df -P -B M / gives a stable, parser-friendly output. Just root for SC2;
        // per-mount is fine future work for the Inventory expansion.
        const r = await runShell('df -P -B M / | tail -n 1', { timeout: 3000 });
        const m = (r.stdout || '').match(/^\S+\s+(\d+)M\s+(\d+)M\s+(\d+)M\s+(\d+)%\s+(\S+)/);
        if (!m) return { mount: '/', usedMb: 0, totalMb: 0, pct: 0, available: false };
        return {
            mount: m[5],
            totalMb: Number(m[1]),
            usedMb: Number(m[2]),
            pct: Number(m[4]),
            available: true,
        };
    });

    ipcMain.handle('system:net', () => {
        // Return raw counters; the renderer diffs successive calls to derive
        // throughput. Keeping main stateless means no background timers that
        // would persist when the System Core screen isn't open.
        if (!IS_LINUX) return { rxBytes: 0, txBytes: 0, t: Date.now(), available: false };
        try {
            const text = fs.readFileSync('/proc/net/dev', 'utf8');
            let rx = 0, tx = 0;
            for (const line of text.split('\n')) {
                const colon = line.indexOf(':');
                if (colon < 0) continue;
                const iface = line.slice(0, colon).trim();
                if (iface === 'lo' || !iface) continue;  // skip loopback
                const cols = line.slice(colon + 1).trim().split(/\s+/).map(Number);
                if (cols.length < 9) continue;
                rx += cols[0] || 0;   // RX bytes
                tx += cols[8] || 0;   // TX bytes
            }
            return { rxBytes: rx, txBytes: tx, t: Date.now(), available: true };
        } catch (e) {
            return { rxBytes: 0, txBytes: 0, t: Date.now(), available: false,
                     error: e.message };
        }
    });

    ipcMain.handle('system:inventory', async () => {
        // Static inventory loaded once per System Core visit. All fields are
        // optional — missing tools/files just produce empty strings instead
        // of failing the whole call.
        const result = {
            hostname: os.hostname(),
            platform: process.platform,
            kernel: os.release(),
            outlawVersion: APP_VERSION,
            packages: 0,
            sessionPref: '',
            snapshotMb: 0,
            apparmor: '',
            ufw: '',
            bootSince: '',
            available: IS_LINUX,
        };
        if (!IS_LINUX) return result;

        // Run the cheap probes in parallel — total wall time ~150–300ms.
        const [pacman, sessPref, snapDu, appArmor, ufwStatus, bootSince] = await Promise.all([
            runShell('pacman -Q 2>/dev/null | wc -l', { timeout: 4000 }),
            runShell('cat "$HOME/.outlaw-session-pref" 2>/dev/null', { timeout: 1000 }),
            // Best-effort snapshot disk usage — only for the installed CodeMaker
            // path. If CodeMaker isn't installed, just return 0.
            runShell(
                'find /opt/outlaw-codemaker -path "*/.outlaw/snapshots" -prune -print 2>/dev/null ' +
                '| xargs -I{} du -sm {} 2>/dev/null | awk "{s+=$1} END {print s+0}"',
                { timeout: 4000 },
            ),
            runShell('systemctl is-active apparmor 2>/dev/null', { timeout: 2000 }),
            runShell('ufw status 2>/dev/null | head -n 1 | sed "s/Status: //"', { timeout: 2000 }),
            runShell('uptime -s 2>/dev/null', { timeout: 1500 }),
        ]);

        result.packages = parseInt(pacman.stdout, 10) || 0;
        result.sessionPref = (sessPref.stdout || '').trim() || 'ask';
        result.snapshotMb = parseInt(snapDu.stdout, 10) || 0;
        result.apparmor = (appArmor.stdout || '').trim() || 'inactive';
        result.ufw = (ufwStatus.stdout || '').trim() || 'inactive';
        result.bootSince = (bootSince.stdout || '').trim();
        return result;
    });

    // ----- SC3 System Core diagnostics ------------------------------------
    // Runs are owned by the singleton _diagRunner. Only one at a time.
    // Streaming progress goes out on 'diagnostics-progress' (whitelisted in
    // preload's EVENT_CHANNELS). Reports persist to ~/.outlaw-diagnostics/.

    ipcMain.handle('diagnostics:run', async (_e, profile) => {
        const runner = getDiagRunner();
        try {
            // Don't await — return immediately so the renderer can show its
            // progress UI. The 'done' progress event signals completion.
            runner.run(profile).catch((err) => {
                if (mainWindow && !mainWindow.isDestroyed()) {
                    mainWindow.webContents.send('diagnostics-progress', {
                        phase: 'error', error: err.message,
                    });
                }
            });
            return { ok: true };
        } catch (e) {
            return { ok: false, error: e.message };
        }
    });

    ipcMain.handle('diagnostics:cancel', () => {
        const runner = getDiagRunner();
        runner.abort();
        return { ok: true };
    });

    ipcMain.handle('diagnostics:status', () => {
        const runner = getDiagRunner();
        return runner.state();
    });

    ipcMain.handle('diagnostics:list-reports', async () => {
        return await listDiagReports();
    });

    ipcMain.handle('diagnostics:read-report', async (_e, filename) => {
        try {
            return { ok: true, report: await readDiagReport(filename) };
        } catch (e) {
            return { ok: false, error: e.message };
        }
    });

    // ----- SC5 Cold-mode TTS --------------------------------------------------
    // Engine detection is cached for 30s inside tts.js. speak() respects the
    // coreVoiceEnabled setting — if the user has the toggle off, the call
    // returns ok:true with did:'muted' so the renderer doesn't treat the
    // no-op as an error.

    ipcMain.handle('tts:status', async (_e, opts) => {
        // Caller can pass {force:true} after installing piper/espeak via the
        // Apps panel to bypass the 30s detection cache without waiting.
        const force = !!(opts && opts.force);
        const eng = await tts.detectEngine(force);
        return { ...eng, enabled: !!settings.coreVoiceEnabled };
    });

    ipcMain.handle('tts:speak', async (_e, text) => {
        if (!settings.coreVoiceEnabled) return { ok: true, did: 'muted' };
        return await tts.speak(text);
    });

    ipcMain.handle('tts:cancel', () => {
        tts.cancel();
        return { ok: true };
    });

    // ----- SC4 Scheduled diagnostic checks --------------------------------
    // The actual timers are systemd user units shipped in airootfs (or copied
    // to ~/.config/systemd/user/ for off-skel users). These handlers just
    // wrap systemctl --user so the renderer never shells out itself.

    const SCHEDULED_PROFILES = {
        // profile id -> { timer unit name, what it runs }
        daily:   { timer: 'outlaw-diagnose-daily.timer',   profile: 'quick'    },
        weekly:  { timer: 'outlaw-diagnose-weekly.timer',  profile: 'standard' },
        monthly: { timer: 'outlaw-diagnose-monthly.timer', profile: 'thorough' },
    };

    async function _schedStatusOne(id) {
        const entry = SCHEDULED_PROFILES[id];
        if (!entry) return null;
        const result = { id, timer: entry.timer, profile: entry.profile,
                         enabled: false, active: false, nextRun: '', lastRun: '',
                         lastResult: '', available: IS_LINUX };
        if (!IS_LINUX) return result;
        const [en, act, show] = await Promise.all([
            runShell(`systemctl --user is-enabled ${entry.timer} 2>/dev/null`, { timeout: 3000 }),
            runShell(`systemctl --user is-active  ${entry.timer} 2>/dev/null`, { timeout: 3000 }),
            // --property gives a parser-friendly key=value dump.
            runShell(
                `systemctl --user show ${entry.timer} --property=NextElapseUSecRealtime,LastTriggerUSec,Result 2>/dev/null`,
                { timeout: 3000 },
            ),
        ]);
        result.enabled = (en.stdout || '').trim() === 'enabled';
        result.active  = (act.stdout || '').trim() === 'active';
        for (const line of (show.stdout || '').split('\n')) {
            const m = line.match(/^([A-Za-z]+)=(.*)$/);
            if (!m) continue;
            const [k, v] = [m[1], m[2].trim()];
            if (k === 'NextElapseUSecRealtime') result.nextRun = v;
            if (k === 'LastTriggerUSec')        result.lastRun = v;
            if (k === 'Result')                 result.lastResult = v;
        }
        return result;
    }

    ipcMain.handle('scheduled:status', async () => {
        const out = {};
        for (const id of Object.keys(SCHEDULED_PROFILES)) {
            out[id] = await _schedStatusOne(id);
        }
        return out;
    });

    ipcMain.handle('scheduled:enable', async (_e, id) => {
        if (!IS_LINUX) return { ok: false, error: 'Scheduled checks run on Outlaw OS.' };
        const entry = SCHEDULED_PROFILES[id];
        if (!entry) return { ok: false, error: 'Unknown schedule id.' };
        // --now also kicks off the timer immediately so it starts counting
        // toward the next OnCalendar trigger.
        const r = await runShell(
            `systemctl --user enable --now ${entry.timer}`,
            { timeout: 8000 },
        );
        if (r.code !== 0) return { ok: false, error: (r.stderr || r.stdout || '').slice(-300) };
        return { ok: true };
    });

    ipcMain.handle('scheduled:disable', async (_e, id) => {
        if (!IS_LINUX) return { ok: false, error: 'Scheduled checks run on Outlaw OS.' };
        const entry = SCHEDULED_PROFILES[id];
        if (!entry) return { ok: false, error: 'Unknown schedule id.' };
        const r = await runShell(
            `systemctl --user disable --now ${entry.timer}`,
            { timeout: 8000 },
        );
        if (r.code !== 0) return { ok: false, error: (r.stderr || r.stdout || '').slice(-300) };
        return { ok: true };
    });

    ipcMain.handle('scheduled:run-now', async (_e, id) => {
        if (!IS_LINUX) return { ok: false, error: 'Scheduled checks run on Outlaw OS.' };
        const entry = SCHEDULED_PROFILES[id];
        if (!entry) return { ok: false, error: 'Unknown schedule id.' };
        // start the instance directly so it runs whether the timer is enabled
        // or not. The user expects "run now" to mean "run now".
        const r = await runShell(
            `systemctl --user start outlaw-diagnose@${entry.profile}.service`,
            { timeout: 5000 },
        );
        if (r.code !== 0) return { ok: false, error: (r.stderr || r.stdout || '').slice(-300) };
        return { ok: true, started: entry.profile };
    });

    // ----- SC7 VRAM tier --------------------------------------------------
    // status() always returns the cached probe when fresh — perfect for the
    // System Core badge that re-asks every visit. `force:true` skips the
    // cache (Settings dropdown change handler uses this so the subtitle
    // updates in the same beat).

    ipcMain.handle('vram:status', async (_e, opts) => {
        const force = !!(opts && opts.force);
        return await vramTier.getStatus(force);
    });

    ipcMain.handle('vram:set-mode', async (_e, mode) => {
        try {
            vramTier.setMode(mode);
            // Persist alongside the rest of settings so the choice survives
            // a shell restart. settings:set would also do this, but Settings
            // UI calls vram:set-mode directly to make the IPC trip shorter.
            settings = saveSettings({ ...settings, vramSaverMode: mode });
            return { ok: true, status: await vramTier.getStatus(true) };
        } catch (e) {
            return { ok: false, error: e.message };
        }
    });

    // ----- SC6 System Core Live AI ----------------------------------------
    // coreai:ask routes a user prompt through LM Studio (via coreai.ask) and
    // then dispatches whatever tool the model picked. ALL tool routes use
    // existing audited code paths — no new privileged endpoints. Refuses to
    // talk to LM Studio at all when VRAM is in the `minimal` tier; the
    // renderer already won't let the user toggle Live mode on in that case,
    // but this is the defence-in-depth backstop.

    ipcMain.handle('coreai:status', async () => {
        const lm = await coreai.status(aiBackend());
        const vram = await vramTier.getStatus();
        return {
            lmStudio: lm,
            vramTier: vram.tier,
            vramLabel: vram.label,
            // Gating intent: emergency tier means Live mode should refuse.
            allowLive: vram.tier !== 'minimal',
        };
    });

    ipcMain.handle('coreai:reset', (_e, sessionId) => {
        coreai.resetSession(sessionId);
        return { ok: true };
    });

    ipcMain.handle('coreai:ask', async (_e, payload) => {
        const { sessionId, text } = payload || {};
        const vram = await vramTier.getStatus();
        if (vram.tier === 'minimal') {
            return {
                ok: false,
                error: 'Core suppressed — VRAM critical. Free some VRAM or change saver mode.',
            };
        }
        const appIds = Object.keys(APP_REGISTRY);
        const r = await coreai.ask({
            sessionId, text, appIds,
            ...aiBackend(),                                  // base Ollama model or LM Studio
            machine: machineSummary(await gatherSpecs()),
        });
        if (!r.ok) return r;

        // Route the tool. Each branch uses the SAME helpers the user-facing
        // IPC handlers do — no new shells out, no new bypass.
        const intent = r.intent;
        let did = 'answer';
        let detail = '';
        try {
            if (intent.tool === 'run_diagnostic') {
                const profile = (intent.arg || '').toLowerCase().trim();
                if (['quick', 'standard', 'thorough'].includes(profile)) {
                    const runner = getDiagRunner();
                    // Fire-and-forget — progress events stream via the
                    // existing 'diagnostics-progress' channel. The Core's
                    // bubble shows intent.text immediately.
                    runner.run(profile).catch((err) => {
                        if (mainWindow && !mainWindow.isDestroyed()) {
                            mainWindow.webContents.send('diagnostics-progress', {
                                phase: 'error', error: err.message,
                            });
                        }
                    });
                    did = 'run_diagnostic';
                    detail = profile;
                } else {
                    did = 'invalid_tool_arg';
                    detail = `unknown profile: ${profile}`;
                }
            } else if (intent.tool === 'launch_app') {
                const id = (intent.arg || '').toLowerCase().trim();
                const entry = APP_REGISTRY[id];
                if (entry) {
                    const bin = await resolveBinary(entry);
                    if (bin) {
                        launchDetached(bin, entry.args || []);
                        did = 'launch_app';
                        detail = entry.label;
                    } else {
                        did = 'app_not_installed';
                        detail = entry.label + ' is not installed';
                    }
                } else {
                    did = 'invalid_tool_arg';
                    detail = `unknown app id: ${id}`;
                }
            } else if (intent.tool === 'notify') {
                if (IS_LINUX) {
                    // Reuse the diagnostics CLI's notify-send pattern.
                    const msg = (intent.arg || intent.text || '').slice(0, 240);
                    spawn('notify-send', [
                        '--app-name=Outlaw Core',
                        '--urgency=normal',
                        'Outlaw Core', msg,
                    ], { stdio: 'ignore', detached: true }).unref();
                    did = 'notify';
                    detail = 'sent';
                } else {
                    did = 'notify_unavailable';
                    detail = 'off-Linux';
                }
            }
        } catch (e) {
            did = 'tool_error';
            detail = e.message;
        }
        return { ok: true, intent, did, detail };
    });

    // ----- Live-ISO detection ---------------------------------------------
    // /run/archiso exists only when booted from the live ISO. On installed
    // systems this returns false even if the user kept the ISO around.
    ipcMain.handle('system:live-iso', () => {
        if (!IS_LINUX) return { live: false, dismissed: !!settings.liveWelcomeDismissed };
        let live = false;
        try { live = fs.existsSync('/run/archiso'); } catch { /* default false */ }
        return { live, dismissed: !!settings.liveWelcomeDismissed };
    });

    // ----- Network / Wi-Fi (nmcli over NetworkManager) ----------------------
    // The live ISO ships networkmanager + linux-firmware, and installs need the
    // network — but there was NO in-OS way to connect to Wi-Fi (the root cause
    // behind several "couldn't reach the package servers" install failures).
    // nmcli terse output separates fields with ':' and escapes literal colons
    // as '\:' — splitTerse handles that.
    const splitTerse = (line) =>
        line.replace(/\\:/g, '\u0000').split(':').map((s) => s.replace(/\u0000/g, ':'));

    ipcMain.handle('net:status', async () => {
        if (!IS_LINUX) return { connectivity: 'unknown', wifi: false, active: '' };
        const [conn, devs] = await Promise.all([
            runShell('nmcli networking connectivity check 2>/dev/null', { timeout: 10000 }),
            runShell('nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev 2>/dev/null', { timeout: 8000 }),
        ]);
        const lines = (devs.stdout || '').split('\n').filter(Boolean).map(splitTerse);
        const wifi = lines.some((t) => t[1] === 'wifi');
        const act = lines.find((t) => t[2] === 'connected' && t[1] !== 'loopback');
        return {
            connectivity: (conn.stdout || '').trim() || 'unknown', // full|limited|portal|none|unknown
            wifi,
            active: act ? `${act[3] || act[0]} (${act[1]})` : '',
        };
    });

    ipcMain.handle('net:wifi-list', async () => {
        if (!IS_LINUX) return { ok: false, networks: [], error: 'Wi-Fi scan runs on Outlaw OS.' };
        await runShell('nmcli radio wifi on 2>/dev/null', { timeout: 5000 });
        // --rescan yes forces a fresh scan; can take a few seconds.
        const r = await runShell("nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list --rescan yes 2>/dev/null", { timeout: 30000 });
        const seen = new Set();
        const networks = [];
        for (const line of (r.stdout || '').split('\n')) {
            if (!line.trim()) continue;
            const t = splitTerse(line);
            const ssid = t[1];
            if (!ssid || seen.has(ssid)) continue; // skip hidden + duplicate APs
            seen.add(ssid);
            networks.push({
                inUse: t[0] === '*',
                ssid,
                signal: parseInt(t[2], 10) || 0,
                security: (t[3] || '').trim(), // '' = open network
            });
        }
        networks.sort((a, b) => (b.inUse - a.inUse) || (b.signal - a.signal));
        return { ok: true, networks };
    });

    ipcMain.handle('net:wifi-connect', async (_e, payload) => {
        if (!IS_LINUX) return { ok: false, error: 'Wi-Fi runs on Outlaw OS.' };
        const ssid = String((payload && payload.ssid) || '');
        const password = String((payload && payload.password) || '');
        if (!ssid || ssid.length > 64) return { ok: false, error: 'Bad network name.' };
        // execFile with an argv array — no shell, so SSIDs/passwords with
        // spaces or quotes can't break out or inject anything.
        const args = ['dev', 'wifi', 'connect', ssid];
        if (password) args.push('password', password);
        const r = await new Promise((resolve) => {
            execFile('nmcli', args, { timeout: 45000 },
                (err, stdout, stderr) => resolve({ err, stdout: stdout || '', stderr: stderr || '' }));
        });
        if (r.err) {
            let msg = (r.stderr || r.stdout || r.err.message).trim().slice(0, 300);
            if (/secrets were required|802-11-wireless-security|invalid/i.test(msg)) {
                msg = 'Wrong password (or the network rejected the connection). Try again.';
            }
            return { ok: false, error: msg };
        }
        return { ok: true, log: (r.stdout || '').trim().slice(0, 300) };
    });

    // ----- Auth: PIN + sign-in (Phase 3c) -----------------------------------
    ipcMain.handle('auth:status', () => ({
        linux: IS_LINUX,
        live: IS_LIVE,
        hasPin: hasPin(),
        // Sign-in lock: on for installed systems, off on the live demo (root,
        // no password). Toggle in Settings.
        lockEnabled: IS_LINUX && !IS_LIVE && settings.lockEnabled !== false,
        user: (() => { try { return os.userInfo().username; } catch { return 'operator'; } })(),
    }));
    ipcMain.handle('auth:set-pin', (_e, payload) => {
        const { pin, current } = payload || {};
        if (hasPin()) {
            const okCur = (current && /^\d{4}$/.test(current)) ? verifyPin(current) : false;
            if (!okCur) return { ok: false, error: 'Your current PIN is incorrect.' };
        }
        return setPin(pin) ? { ok: true } : { ok: false, error: 'The PIN must be exactly 4 digits.' };
    });
    ipcMain.handle('auth:clear-pin', async (_e, payload) => {
        const u = await authUnlock(payload || {});
        if (!u.ok) return u;
        clearPin();
        return { ok: true };
    });
    ipcMain.handle('auth:unlock', async (_e, payload) => authUnlock(payload || {}));
    ipcMain.handle('auth:set-lock', (_e, enabled) => {
        settings = saveSettings({ ...settings, lockEnabled: !!enabled });
        return { ok: true };
    });

    ipcMain.handle('files:home', () => os.homedir());
    ipcMain.handle('files:list', (_e, dir) => listFiles(dir || os.homedir()));
    ipcMain.handle('files:open', (_e, target) => openPath(target));
    // Open a real file manager (Thunar) at a path so the user can actually
    // open / copy / rename / right-click files. The built-in list is a quick
    // viewer; opening a file with shell.openPath needs a registered handler,
    // which a fresh system lacks — Thunar gives full interaction either way.
    ipcMain.handle('files:open-manager', async (_e, dir) => {
        if (!IS_LINUX) return { ok: false, error: 'Runs on Outlaw OS.' };
        const target = (dir && typeof dir === 'string') ? dir : os.homedir();
        const bin = await resolveBinary(APP_REGISTRY.files);
        if (bin) {
            launchDetached(bin, [target], { focus: String(bin).split('/').pop() });
            return { ok: true };
        }
        return openPath(target); // last resort: xdg-open the folder
    });

    ipcMain.handle('apps:list', () =>
        Object.entries(APP_REGISTRY).map(([id, v]) => ({ id, label: v.label })));

    ipcMain.handle('apps:launch', async (_e, id) => {
        // Primary lookup: the curated quick-launch registry (the OS's "always
        // there" apps the AI is allowed to open).
        let entry = APP_REGISTRY[id];
        // Fallback: the on-demand catalog — apps installed via the Apps panel
        // may not be in APP_REGISTRY but still have a `bin` we can launch.
        if (!entry) {
            const cat = APP_CATALOG.find((a) => a.id === id && a.bin);
            if (cat) entry = { label: cat.label, bin: cat.bin, args: [] };
        }
        if (!entry) return { ok: false, error: 'Unknown app.' };
        const bin = await resolveBinary(entry);
        if (!bin) return { ok: false, error: `${entry.label} is not installed.` };
        launchDetached(bin, entry.args || []);
        return { ok: true, label: entry.label };
    });

    // ----- Apps catalog (on-demand installs via pkexec pacman) -----

    ipcMain.handle('apps:catalog', () => {
        // Only return UI-relevant fields. Renderer never sees raw pacman pkg names
        // (they can't be tampered with anyway — the install handler only honors `id`).
        return APP_CATALOG.map((a) => ({
            id: a.id,
            label: a.label,
            category: a.category,
            description: a.description,
            launchable: !!a.bin,
        }));
    });

    ipcMain.handle('apps:installed-list', async () => {
        if (!IS_LINUX) {
            // Off-Linux preview: nothing is "installed".
            return APP_CATALOG.map((a) => ({ id: a.id, installed: false }));
        }
        // pacman -Qq <pkg1> <pkg2> ... in a single call. Missing packages go to
        // stderr; installed ones go to stdout. We just need the set of installed.
        const pkgs = APP_CATALOG.map((a) => a.pkg).join(' ');
        const r = await runShell(`pacman -Qq ${pkgs} 2>/dev/null`, { timeout: 8000 });
        const installed = new Set(r.stdout.split('\n').filter(Boolean));
        return APP_CATALOG.map((a) => ({ id: a.id, installed: installed.has(a.pkg) }));
    });

    // Phase 15c — search ALL official packages (not just the curated catalog), so
    // the user can install anything. Read-only `pacman -Ss`; the query is strictly
    // validated (must start alphanumeric, safe charset) and each term single-quoted
    // so it can never be a shell injection or a stray pacman flag.
    ipcMain.handle('apps:search', async (_e, query) => {
        if (!IS_LINUX) return { ok: false, error: 'Search runs on Outlaw OS.', results: [] };
        const q = String(query || '').trim();
        if (q.length < 2) return { ok: true, results: [] };
        if (!/^[a-z0-9][a-z0-9 ._+-]{0,39}$/i.test(q)) {
            return { ok: false, error: 'Search with letters, numbers, spaces or . _ + - only.', results: [] };
        }
        const terms = q.split(/\s+/).filter(Boolean).map((w) => `'${w}'`).join(' ');
        const r = await runShell(`pacman -Ss ${terms}`, { timeout: 12000 });
        const lines = (r.stdout || '').split('\n');
        const results = [];
        for (let i = 0; i < lines.length && results.length < 30; i++) {
            const m = lines[i].match(/^(\w[\w-]*)\/(\S+)\s+(\S+)(.*)$/);
            if (m) {
                results.push({
                    repo: m[1],
                    name: m[2],
                    version: m[3],
                    installed: /\[installed/.test(m[4] || ''),
                    description: (lines[i + 1] || '').trim(),
                });
            }
        }
        return { ok: true, results };
    });

    // Phase 15c — install any official package by name (the "install anything" path
    // behind the search results above). Name is strictly validated, then verified
    // to be a real repo package before we hand it to the privileged installer.
    ipcMain.handle('apps:install-pkg', async (_e, pkg) => {
        if (!IS_LINUX) return { ok: false, error: 'Install runs on Outlaw OS.' };
        const name = String(pkg || '').trim();
        if (!/^[a-z0-9][a-z0-9@._+-]{0,79}$/i.test(name)) return { ok: false, error: 'Invalid package name.' };
        const info = await runShell(`pacman -Si '${name}'`, { timeout: 8000 });
        if (info.code !== 0) return { ok: false, error: `"${name}" isn't an available package.` };
        const r = await privInstall(name, 1000 * 60 * 20);
        const tail = (r.stdout || r.stderr || `exit ${r.code}`).slice(-3000);
        if (r.code !== 0) return { ok: false, error: tail };
        return { ok: true, text: `${name} installed.` };
    });

    ipcMain.handle('apps:install', async (_e, id) => {
        if (!IS_LINUX) return { ok: false, error: 'Install runs on Outlaw OS.' };
        const app = APP_CATALOG.find((a) => a.id === id);
        if (!app) return { ok: false, error: 'Unknown app id.' };
        // Route through the outlaw-pkg-install helper (via pkexec). It enables
        // [multilib] on demand for Steam / lib32 packages, force-refreshes the
        // databases (fixes "failed to synchronize databases"), inits the keyring
        // if empty, then installs. `extra` packages install with the primary;
        // install-state is still tracked on the primary `pkg`.
        // 20-minute timeout — large multilib packages on slow connections.
        const pkgList = [app.pkg, ...(app.extra || [])].join(' ');
        // Resilient: uses the installed helper (passwordless) if present, else a
        // temp-script fallback — so a missing helper can't break installs.
        const r = await privInstall(pkgList, 1000 * 60 * 20);
        const tail = (r.stdout || r.stderr || `exit ${r.code}`).slice(-3000);
        if (r.code !== 0) {
            // Surface a useful hint for the most common failure modes.
            const hint = /could not synchronize|failed to synchronize|database file for|use ['"]?-Sy/i.test(tail)
                ? '\n\nHint: couldn\'t reach the package servers. Open Settings → Network & Wi-Fi, get online, then try again.'
                : /target not found|could not find/i.test(tail)
                    ? '\n\nHint: a package wasn\'t found. If this is Steam, the multilib repo is needed — the installer enables it automatically, so just try once more.'
                    : /not authorized|authentication agent|polkit|dismissed/i.test(tail)
                        ? '\n\nHint: the password prompt was cancelled or no authorization agent answered.'
                        : '';
            return { ok: false, error: tail + hint };
        }
        return { ok: true, label: app.label, log: tail };
    });

    ipcMain.handle('apps:uninstall', async (_e, id) => {
        if (!IS_LINUX) return { ok: false, error: 'Uninstall runs on Outlaw OS.' };
        const app = APP_CATALOG.find((a) => a.id === id);
        if (!app) return { ok: false, error: 'Unknown app id.' };
        // -Rs removes the package + any deps that become orphaned (safe).
        // Not -Rsc (cascade) — that's too aggressive and could remove something
        // the user still wants.
        const cmd = `pkexec pacman -Rs --noconfirm ${app.pkg}`;
        const r = await runShell(cmd, { timeout: 1000 * 60 * 10 });
        const tail = (r.stdout || r.stderr || `exit ${r.code}`).slice(-2000);
        return { ok: r.code === 0, label: app.label, log: tail, error: r.code === 0 ? '' : tail };
    });

    ipcMain.handle('apps:refresh-db', async () => {
        // Optional: refresh local pacman DB without doing a full upgrade.
        // Mildly risky (partial-upgrade window) but useful before an Apps install.
        if (!IS_LINUX) return { ok: false, error: 'Runs on Outlaw OS.' };
        const r = await runShell('pkexec pacman -Sy --noconfirm', { timeout: 1000 * 60 * 5 });
        return { ok: r.code === 0, log: (r.stdout || r.stderr).slice(-2000) };
    });

    // ----- App auto-discovery (Phase 2): apps the user installed themselves --
    ipcMain.handle('apps:discover', async () => {
        try { return discoverApps(); } catch { return []; }
    });

    ipcMain.handle('apps:launch-discovered', async (_e, id) => {
        if (!IS_LINUX) return { ok: false, error: 'Runs on Outlaw OS.' };
        // Re-scan and match by id so the renderer can only launch something that
        // genuinely exists on disk — never an arbitrary command from the page.
        const item = discoverApps().find((a) => a.id === id);
        if (!item) return { ok: false, error: 'App not found — try refreshing.' };
        try {
            if (item.kind === 'appimage') {
                try { fs.chmodSync(item.path, 0o755); } catch {}
                launchDetached(item.path, [], { focus: path.basename(item.path) });
                return { ok: true, label: item.name };
            }
            const parts = _splitExec(_cleanExec(item.exec));
            if (!parts.length) return { ok: false, error: 'No runnable command in the .desktop entry.' };
            const bin = parts[0];
            const args = parts.slice(1);
            if (item.terminal) {
                // Terminal apps need a terminal + focus (no WM) — reuse outlaw-term.
                launchDetached('outlaw-term', [item.name, bin, ...args], { focus: false });
            } else {
                launchDetached(bin, args, { focus: path.basename(bin) });
            }
            return { ok: true, label: item.name };
        } catch (err) {
            return { ok: false, error: err.message };
        }
    });

    // Inspect a command WITHOUT running it (UI uses this to warn before submit).
    ipcMain.handle('terminal:inspect', (_e, command) => classifyCommand(command));

    ipcMain.handle('terminal:run', async (_e, { command, opts }) => {
        const cls = classifyCommand(command);
        if (cls.danger && !(opts && opts.confirmDangerous)) {
            return { code: -1, stdout: '', stderr: '', blocked: true, reason: cls.reason };
        }
        const r = await runShell(command, { timeout: 120000 });
        return { ...r, blocked: false, danger: cls.danger };
    });

    ipcMain.handle('settings:get', () => settings);
    ipcMain.handle('settings:set', (_e, patch) => {
        const before = {
            autoCheck: settings.autoCheck,
            updateRepo: settings.updateRepo,
            vramSaverMode: settings.vramSaverMode,
        };
        settings = saveSettings({ ...settings, ...(patch || {}) });
        // If updater config changed, restart the background timer accordingly.
        if (before.autoCheck !== settings.autoCheck || before.updateRepo !== settings.updateRepo) {
            startAutoCheck();
        }
        // SC7 — apply VRAM saver mode flips immediately so the tier badge,
        // any subscribed renderers, and the cached probe all catch up
        // without waiting for the next 10s background poll.
        if (before.vramSaverMode !== settings.vramSaverMode) {
            try { vramTier.setMode(settings.vramSaverMode); }
            catch (e) { console.warn('vramTier.setMode rejected:', e.message); }
        }
        return settings;
    });

    // --- AI ---
    ipcMain.handle('ai:status', async () => {
        const be = aiBackend();
        const s = await aiAgent.status(be);
        return { ...s, enabled: settings.aiEnabled, model: be.model, backend: be.kind,
                 baseAiEnabled: settings.baseAiEnabled !== false };
    });

    ipcMain.handle('ai:enable', async () => {
        // LM Studio is a user-launched desktop app, not a systemd service we own.
        // Enabling here means "the shell will route prompts to it"; the actual
        // server is started by the user in LM Studio's UI ("Start Server"). We
        // try launching the LM Studio helper as a convenience, but don't gate
        // success on it — the user may already have LM Studio running.
        settings = saveSettings({ ...settings, aiEnabled: true });
        const be = aiBackend();
        if (IS_LINUX) {
            // Fire-and-forget — never block the IPC reply on it.
            if (be.kind === 'base') ensureBaseModel().catch(() => {});       // built-in: pull model if missing
            else runShell('outlaw-lm-studio 2>/dev/null &', { timeout: 2000 }).catch(() => {});  // fallback: launch LM Studio
        }
        const s = await aiAgent.status(be);
        return { ok: true, enabled: true, available: s.available, model: be.model, backend: be.kind };
    });

    // Phase 13.2: pull the built-in base model on demand (first desktop run).
    ipcMain.handle('ai:ensure-base-model', async () => ensureBaseModel());

    ipcMain.handle('ai:disable', async () => {
        // Just flips the routing bit — we don't kill LM Studio, the user owns it.
        settings = saveSettings({ ...settings, aiEnabled: false });
        return { ok: true, enabled: false };
    });

    // Phase 4: read this PC's specs and recommend a local model + settings.
    ipcMain.handle('ai:recommend', async (_e, opts) => {
        // Phase 14d: opts = { purpose:'desktop'|'dev', tier, spill }. Specs are
        // cached; recompute the recommendation fresh for the chosen purpose so the
        // dev-vs-desktop + powerful/minimal + spill choices are honoured.
        const s = await gatherSpecs();
        const rec = recommendModel(s.ramGb, s.vramGb, opts || {});
        return { ok: true, ...s, ...rec };
    });

    // Phase 4: hardware-aware setup guide. A plain-prose chat (NOT the JSON-intent
    // agent) whose system prompt already knows this PC's specs + the recommended
    // model, so even a tiny local model can walk the user through getting AI
    // running. Degrades to a clear "load the starter model first" hint when
    // LM Studio isn't serving yet.
    ipcMain.handle('ai:setup-chat', async (_e, payload) => {
        const userMsg = String((payload && payload.prompt) || '').slice(0, 2000).trim();
        if (!userMsg) return { ok: false, error: 'Ask a question first.' };
        const s = await gatherSpecs();
        const sys = [
            "You are OUTLAW's on-device AI setup guide. You help the operator get a "
                + 'private local AI running in LM Studio on THIS computer — everything '
                + 'stays local, no account or internet needed.',
            'This computer: ' + machineSummary(s),
            'Recommend the model above. If the machine is weak, steer them to the '
                + 'starter model first. Be friendly and practical: short answers, a '
                + 'short numbered list when giving steps. Plain text only — no JSON, no '
                + 'code fences unless quoting an exact setting value.',
        ].join('\n');
        const prior = Array.isArray(payload && payload.history)
            ? payload.history
                .filter((t) => t && (t.role === 'user' || t.role === 'assistant') && typeof t.content === 'string')
                .slice(-6)
            : [];
        const messages = [{ role: 'system', content: sys }, ...prior, { role: 'user', content: userMsg }];
        try {
            const text = await aiAgent.chat(messages, { ...aiBackend(), maxTokens: 420 });
            return { ok: true, text: text || 'No reply.' };
        } catch (e) {
            return {
                ok: false,
                error: settings.baseAiEnabled !== false
                    ? 'The built-in AI isn\'t ready yet — it may still be downloading its model. Try again shortly.'
                    : 'LM Studio isn\'t answering yet. Install it, load the starter model ('
                        + s.starter.model + '), click "Start Server", then ask again.',
            };
        }
    });

    ipcMain.handle('ai:ask', async (_e, payload) => {
        if (!settings.aiEnabled) return { error: 'AI is disabled. Enable it in Settings.' };
        // payload is a plain string (legacy) or { prompt, history, summary } so a
        // persistent chat can give Cr1tt3r conversation memory (Phase 15b).
        const prompt = typeof payload === 'string' ? payload : ((payload && payload.prompt) || '');
        const history = (payload && Array.isArray(payload.history)) ? payload.history : [];
        const summary = (payload && typeof payload.summary === 'string') ? payload.summary : '';
        const be = aiBackend();
        const s = await aiAgent.status(be);
        if (!s.available) {
            return {
                error: be.kind === 'base'
                    ? 'The built-in AI isn\'t ready yet — it may still be downloading its model. Try again shortly (or check that Ollama is running).'
                    : 'LM Studio isn\'t reachable. Open LM Studio, load a model, then click "Start Server" (port 1234).',
            };
        }
        try {
            const appIds = Object.keys(APP_REGISTRY);
            const machine = machineSummary(await gatherSpecs());
            const intent = await aiAgent.ask(prompt, { ...be, appIds, machine, history, summary });
            return await executeIntent(intent);
        } catch (e) {
            return { error: e.message };
        }
    });

    // Phase 15b — persistent AI chats: load/save the whole store (small JSON).
    ipcMain.handle('ai:chats:load', () => loadAiChats());
    ipcMain.handle('ai:chats:save', (_e, store) => ({ ok: saveAiChats(store) }));

    // Phase 15b (slice 2) — fold older turns into a running summary so long chats
    // keep memory without resending everything. Best-effort: on any failure the
    // caller keeps its prior summary. payload = { messages:[{role,content}], priorSummary }.
    ipcMain.handle('ai:summarize', async (_e, payload) => {
        const prior = (payload && typeof payload.priorSummary === 'string') ? payload.priorSummary : '';
        if (!settings.aiEnabled) return { summary: prior };
        const msgs = (payload && Array.isArray(payload.messages)) ? payload.messages : [];
        if (!msgs.length) return { summary: prior };
        const be = aiBackend();
        const s = await aiAgent.status(be);
        if (!s.available) return { summary: prior };
        try {
            const convo = msgs
                .map((m) => (m.role === 'user' ? 'User: ' : 'Cr1tt3r: ') + String(m.content || ''))
                .join('\n');
            const prompt = [
                { role: 'system', content: 'You keep a terse running summary of a chat. Preserve names, decisions, facts, and any unfinished threads. Reply with 4–8 short bullet points only — no preamble.' },
                { role: 'user', content: (prior ? 'Current summary:\n' + prior + '\n\n' : '') + 'New turns to fold in:\n' + convo + '\n\nReturn the updated summary as bullets.' },
            ];
            const summary = await aiAgent.chat(prompt, { ...be, maxTokens: 320 });
            return { summary: String(summary || prior || '').slice(0, 2000) };
        } catch {
            return { summary: prior };
        }
    });

    ipcMain.handle('ai:confirm-action', async (_e, action) => {
        if (action && action.tool === 'run_command') {
            const r = await runShell(action.arg, { timeout: 120000 });
            return { text: (r.stdout || r.stderr || `(exit ${r.code})`).slice(0, 4000), did: 'run_command' };
        }
        // Phase 13: install a known-source app the user just approved. Streams to
        // the loading screen via runStreamingJob; uses the robust pkg helper.
        if (action && action.tool === 'install_app') {
            if (!IS_LINUX) return { ok: false, text: 'Installs run on Outlaw OS.', did: 'install_app' };
            const pkgs = [action.pkg, ...(Array.isArray(action.extra) ? action.extra : [])]
                .filter((p) => typeof p === 'string' && /^[a-z0-9][a-z0-9._+-]*$/.test(p));
            if (!pkgs.length) return { ok: false, text: 'Nothing valid to install.', did: 'install_app' };
            const labels = ['Preparing', 'Refreshing databases', `Installing ${action.label || action.pkg}`, 'Finishing'];
            const matchers = [null, /Synchronizing|Refreshing|multilib|keyring/i, /Installing|downloading|reinstalling|^:: |\(\d+\/\d+\)/i, /^>> Installing|installation finished|complete/i];
            const r = await runStreamingJob('pkexec', ['/usr/local/bin/outlaw-pkg-install', ...pkgs], labels, matchers);
            return { ok: r.ok, did: 'install_app',
                     text: r.ok ? `Installed ${action.label || action.pkg}.` : 'Install failed — see the log.' };
        }
        return { text: 'Nothing to do.' };
    });

    // --- Gaming ---
    ipcMain.handle('gaming:status', async () => {
        const out = { gamemode: false, mangohud: false, gpu: '' };
        if (IS_LINUX) {
            out.gamemode = !!(await which('gamemoded'));
            out.mangohud = !!(await which('mangohud'));
            const r = await runShell("lspci 2>/dev/null | grep -Ei 'vga|3d' | sed 's/^.*: //' | head -n 1");
            out.gpu = r.stdout;
        }
        return out;
    });

    ipcMain.handle('gaming:performance', async (_e, on) => {
        settings = saveSettings({ ...settings, performanceMode: !!on });
        if (IS_LINUX) {
            // Best-effort governor switch via the polkit-allowed helper.
            await runShell(`pkexec /usr/local/bin/outlaw-perf ${on ? 'performance' : 'schedutil'} 2>/dev/null || true`, { timeout: 8000 });
        }
        return { ok: true, performanceMode: settings.performanceMode };
    });

    // --- Power / hotswap ---
    ipcMain.handle('power:boot-targets', async () => {
        if (!IS_LINUX) return [];
        const r = await runShell('outlaw-hotswap --list 2>/dev/null');
        return r.stdout.split('\n').filter(Boolean);
    });
    ipcMain.handle('power:hotswap', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Hotswap runs on Outlaw OS.' };
        launchDetached('outlaw-term', ['Outlaw Hotswap', 'outlaw-hotswap'], { focus: false });
        return { ok: true };
    });
    ipcMain.handle('power:reboot', async () => { if (IS_LINUX) await runShell('systemctl reboot'); return { ok: true }; });
    ipcMain.handle('power:shutdown', async () => { if (IS_LINUX) await runShell('systemctl poweroff'); return { ok: true }; });

    // --- Updates / installer ---
    ipcMain.handle('updates:check', async () => {
        if (!IS_LINUX) return { updates: 0, note: 'Updates run on Outlaw OS.' };
        // checkupdates (pacman-contrib) counts updates safely without touching
        // the live DB. It exits 2 when there are none (→ 0 lines, fine). If it's
        // somehow missing, say so instead of silently reporting 0.
        const has = await runShell('command -v checkupdates >/dev/null 2>&1 && echo yes');
        if (!/yes/.test(has.stdout || '')) return { updates: 0, note: 'Update check tool missing — run a full update to refresh.' };
        const r = await runShell('checkupdates 2>/dev/null | grep -c .', { timeout: 1000 * 60 });
        return { updates: parseInt((r.stdout || '0').trim(), 10) || 0 };
    });
    ipcMain.handle('updates:apply', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Updates run on Outlaw OS.' };
        // Full system upgrade via the helper (ABSOLUTE path; passwordless via
        // the 49-outlaw polkit rule). -Syu is the only safe way to update on
        // Arch and covers every app installed from the Apps panel too.
        const r = await privUpdate(1000 * 60 * 30);
        const tail = (r.stdout || r.stderr || `exit ${r.code}`).slice(-4000);
        if (r.code !== 0) {
            const hint = /could not|failed to synchronize|connect|network/i.test(tail)
                ? ' (couldn\'t reach the servers — check Settings → Network & Wi-Fi)' : '';
            return { ok: false, error: tail, hint };
        }
        return { ok: true, log: tail };
    });

    // Shell self-updater (downloads from your GitHub Releases).
    ipcMain.handle('updates:check-shell', async () => {
        try {
            const info = await updater.checkShellUpdate({
                repo: settings.updateRepo,
                currentVersion: APP_VERSION,
                channel: settings.updateChannel || 'stable',
            });
            settings = saveSettings({ ...settings, lastUpdateCheck: Date.now() });
            return { ok: true, ...info };
        } catch (e) {
            return { ok: false, error: e.message };
        }
    });

    ipcMain.handle('updates:install-shell', async (_e, info) => {
        try {
            if (!info || !info.assetUrl) return { ok: false, error: 'No update payload supplied.' };
            const { tarPath, sha } = await updater.downloadShellUpdate(info);
            if (!IS_LINUX) {
                return { ok: false, error: `Downloaded to ${tarPath}. Installation step only runs on Outlaw OS.` };
            }
            // The privileged helper verifies SHA again, extracts atomically, and swaps /usr/share/outlaw-os.
            const cmd = `pkexec /usr/local/bin/outlaw-update-apply ${JSON.stringify(tarPath)} ${JSON.stringify(sha)}`;
            const r = await runShell(cmd, { timeout: 1000 * 60 * 10 });
            if (r.code !== 0) return { ok: false, error: (r.stderr || r.stdout || `exit ${r.code}`).slice(-2000) };
            return { ok: true, log: (r.stdout || '').slice(-2000), restart: true };
        } catch (e) {
            return { ok: false, error: e.message };
        }
    });

    // Rollback: swap /usr/share/outlaw-os with .prev. Used by the Rollback
    // button below the updater. We probe for availability first so the button
    // can be disabled when there's nothing to roll back to.
    ipcMain.handle('updates:rollback-check', async () => {
        if (!IS_LINUX) return { available: false, note: 'Rollback runs on Outlaw OS.' };
        // The .prev directory is owned by root and not world-readable in places;
        // probe it via a tiny shell test instead of fs.access (which would EACCES
        // for non-root readers).
        const r = await runShell('test -d /usr/share/outlaw-os.prev && test -f /usr/share/outlaw-os.prev/main.js && echo yes', { timeout: 4000 });
        return { available: (r.stdout || '').trim() === 'yes' };
    });

    ipcMain.handle('updates:rollback', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Rollback runs on Outlaw OS.' };
        const r = await runShell('pkexec /usr/local/bin/outlaw-update-rollback', { timeout: 1000 * 60 * 2 });
        if (r.code !== 0) return { ok: false, error: (r.stderr || r.stdout || `exit ${r.code}`).slice(-2000) };
        return { ok: true, log: (r.stdout || '').slice(-2000), restart: true };
    });
    ipcMain.handle('installer:launch', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Installer runs from the live boot media.' };
        // outlaw-install-gui opens the point-and-click installer wizard (and
        // handles its own focus poke + falls back to the terminal installer if
        // the GUI app is missing). focus:false — the launcher does it itself.
        launchDetached('outlaw-install-gui', [], { focus: false });
        return { ok: true };
    });

    // Advisory community-stability tally for the INSTALLED version. Reads the
    // public 👍/👎 reaction counts on the matching GitHub release. The local
    // "your vote" is persisted client-side in settings (stabilityReports);
    // this handler only fetches the shared signal. Read-only, no auth.
    ipcMain.handle('stability:tally', async () => {
        try {
            const t = await updater.getStabilityTally({
                repo: settings.updateRepo,
                version: APP_VERSION,
            });
            return { ok: true, version: updater.normalize(APP_VERSION), ...t };
        } catch (e) {
            return { ok: false, error: e.message };
        }
    });

    // Phase 7: build a pre-filled GitHub issue so a "works / broken" report
    // actually reaches the maintainer with context. Server-less: we just return
    // the URL; the user reviews + submits on GitHub. The Reporter ID is an
    // anonymous, stable-per-machine hash so duplicate reports can be merged.
    ipcMain.handle('stability:report-url', async (_e, verdict) => {
        const repo = (settings.updateRepo || '').trim();
        if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) {
            return { ok: false, error: 'Set the GitHub repository in Settings first.' };
        }
        const v = updater.normalize(APP_VERSION);
        const broken = verdict === 'broken';
        // Anonymous machine fingerprint (a hash, never the raw id) for de-dup.
        let mid = '';
        try { if (IS_LINUX) mid = fs.readFileSync('/etc/machine-id', 'utf8').trim(); } catch {}
        if (!mid) mid = os.hostname() || 'unknown';
        const rid = crypto.createHash('sha256').update('outlaw:' + mid).digest('hex').slice(0, 8);
        let cpu = (os.cpus()[0] || {}).model || 'CPU', cores = os.cpus().length;
        let gpu = 'unknown', ram = 'unknown', kernel = os.release();
        try {
            const s = await gatherSpecs();
            cpu = s.cpu || cpu; cores = s.cores || cores;
            gpu = s.gpuName || 'unknown'; if (s.vramGb) gpu += ` (${s.vramGb} GB VRAM)`;
            ram = (s.ramGb || '?') + ' GB';
        } catch {}
        if (IS_LINUX) { try { const k = await runShell('uname -r'); if (k.code === 0 && k.stdout) kernel = k.stdout; } catch {} }
        let safeGfx = false;
        try { safeGfx = fs.existsSync(path.join(os.homedir(), '.outlaw-safe-gfx')); } catch {}
        const title = `[build report] v${v} — ${broken ? 'broken' : 'works'}`;
        const body = [
            '### Build report',
            '',
            `- **Verdict:** ${broken ? '❌ Broken' : '✅ Works'}`,
            `- **Version:** v${v} (${settings.updateChannel || 'stable'} channel)`,
            `- **Reporter ID:** \`${rid}\` (anonymous, stable per machine — for de-duplication)`,
            '',
            '**System**',
            `- CPU: ${cpu} (${cores} cores)`,
            `- GPU: ${gpu}`,
            `- RAM: ${ram}`,
            `- Kernel: ${kernel}`,
            `- Safe graphics: ${safeGfx ? 'yes' : 'no'}`,
            '',
            broken ? '**What went wrong?** (steps, error text, screenshots if you can)'
                   : '**Anything to add?** (optional)',
            '> ',
            '',
            '<!-- generated by Outlaw OS · Help Test This Version -->',
        ].join('\n');
        const url = `https://github.com/${repo}/issues/new`
            + `?title=${encodeURIComponent(title)}`
            + `&body=${encodeURIComponent(body)}`
            + `&labels=${encodeURIComponent('build-report')}`;
        return { ok: true, url };
    });

    // --- Phase 9: session graphics/driver profiles --------------------------
    // outlaw-driver-profile installs USERSPACE graphics packages only (Vulkan /
    // Mesa / 32-bit libs / GameMode) — never kernel modules, KMS or the
    // bootloader, so it can't affect booting. detect/packages are read-only;
    // apply/revert self-elevate via pkexec (passwordless polkit allowlist).
    const DRIVER_PROFILE = '/usr/local/bin/outlaw-driver-profile';
    ipcMain.handle('drivers:detect', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Graphics profiles run on Outlaw OS.' };
        const r = await runShell(`${DRIVER_PROFILE} detect`, { timeout: 8000 });
        try { return { ok: true, ...JSON.parse(r.stdout || '{}') }; }
        catch { return { ok: false, error: 'Could not detect the graphics hardware.' }; }
    });
    ipcMain.handle('drivers:preview', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Graphics profiles run on Outlaw OS.' };
        const r = await runShell(`${DRIVER_PROFILE} packages`, { timeout: 8000 });
        return { ok: true, packages: (r.stdout || '').trim().split(/\s+/).filter(Boolean) };
    });
    ipcMain.handle('drivers:apply', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Graphics profiles run on Outlaw OS.' };
        // Phase 12: stream to the loading screen (live phases + log) instead of a
        // single blocking call with output dumped at the end.
        const labels = ['Preparing', 'Refreshing databases', 'Installing graphics packages', 'Finishing'];
        const matchers = [
            null,
            /Synchronizing|Refreshing|multilib|keyring/i,
            /Installing|downloading|reinstalling|^:: |\(\d+\/\d+\)/i,
            /^>> Done|installation finished|complete/i,
        ];
        return runStreamingJob('pkexec', [DRIVER_PROFILE, 'apply', 'gaming'], labels, matchers);
    });
    ipcMain.handle('drivers:revert', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Graphics profiles run on Outlaw OS.' };
        const r = await runShell(`pkexec ${DRIVER_PROFILE} revert`, { timeout: 1000 * 60 * 5 });
        return r.code === 0
            ? { ok: true, output: (r.stdout || '').slice(-800) }
            : { ok: false, error: (r.stderr || r.stdout || 'Revert failed.').slice(-800) };
    });

    // --- Phase 5: per-machine tuning (outlaw-tune) --------------------------
    // probe / recommend / stress / status are read-only (no pkexec). apply +
    // reset are privileged and go through the audited outlaw-tune helper, which
    // only ever writes its own fixed set of files. JSON is parsed here so the
    // renderer gets objects, not raw text.
    const _parseJson = (s) => { try { return JSON.parse(s); } catch { return null; } };

    ipcMain.handle('tune:probe', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Hardware tuning runs on Outlaw OS.' };
        const r = await runShell('outlaw-tune probe', { timeout: 15000 });
        const data = _parseJson(r.stdout || '');
        return data ? { ok: true, data } : { ok: false, error: (r.stderr || 'probe failed').slice(-400) };
    });

    ipcMain.handle('tune:recommend', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Hardware tuning runs on Outlaw OS.' };
        const r = await runShell('outlaw-tune recommend json', { timeout: 15000 });
        const data = _parseJson(r.stdout || '');
        return data ? { ok: true, data } : { ok: false, error: (r.stderr || 'recommend failed').slice(-400) };
    });

    ipcMain.handle('tune:stress', async (_e, seconds) => {
        if (!IS_LINUX) return { ok: false, error: 'The stress test runs on Outlaw OS.' };
        // Clamp here too; the helper clamps again as defence in depth.
        let s = parseInt(seconds, 10); if (!Number.isFinite(s)) s = 10;
        s = Math.max(3, Math.min(30, s));
        const r = await runShell(`outlaw-tune stress ${s}`, { timeout: (s + 20) * 1000 });
        const data = _parseJson(r.stdout || '');
        return data ? { ok: true, data } : { ok: false, error: (r.stderr || 'stress test failed').slice(-400) };
    });

    ipcMain.handle('tune:status', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Hardware tuning runs on Outlaw OS.' };
        const r = await runShell('outlaw-tune status', { timeout: 8000 });
        return { ok: true, data: _parseJson(r.stdout || '') || { applied: false } };
    });

    ipcMain.handle('tune:apply', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Hardware tuning runs on Outlaw OS.' };
        const r = await runShell('pkexec /usr/local/bin/outlaw-tune apply', { timeout: 1000 * 60 * 2 });
        if (r.code !== 0) return { ok: false, error: (r.stderr || r.stdout || `exit ${r.code}`).slice(-800) };
        // The helper prints OUTLAW_VRAM_MODE=<mode>; mirror it into the shell's
        // own VRAM-saver setting so CodeMaker's default matches the hardware.
        const m = (r.stdout || '').match(/OUTLAW_VRAM_MODE=(\w+)/);
        if (m && ['auto', 'off', 'lean', 'minimal'].includes(m[1])) {
            settings = saveSettings({ ...settings, vramSaverMode: m[1] });
            try { vramTier.setMode(m[1]); } catch {}
        }
        return { ok: true, log: (r.stdout || '').slice(-800) };
    });

    ipcMain.handle('tune:reset', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Hardware tuning runs on Outlaw OS.' };
        const r = await runShell('pkexec /usr/local/bin/outlaw-tune reset', { timeout: 1000 * 60 });
        if (r.code !== 0) return { ok: false, error: (r.stderr || r.stdout || `exit ${r.code}`).slice(-800) };
        return { ok: true, log: (r.stdout || '').slice(-400) };
    });

    // --- Safe mode marker (set by outlaw-session-watchdog after a crash loop) ----
    ipcMain.handle('safe-mode:check', () => {
        if (!IS_LINUX) return { active: false, reason: '' };
        const markerPath = path.join(os.homedir(), '.outlaw-safe-mode');
        try {
            if (!fs.existsSync(markerPath)) return { active: false, reason: '' };
            const reason = fs.readFileSync(markerPath, 'utf8').trim();
            // Delete after reading — banner is one-shot. If the user enters
            // another crash loop, the watchdog writes a fresh marker.
            try { fs.unlinkSync(markerPath); } catch { /* ignored */ }
            return { active: true, reason };
        } catch {
            return { active: false, reason: '' };
        }
    });

    // --- Emergency stop (Ctrl+Alt+K from the renderer) ---------------------
    // Kills every tracked subprocess we've spawned. Last-resort escape hatch
    // for hung pacman installs, runaway terminal commands, etc.
    ipcMain.handle('emergency:stop', () => {
        const n = killAllTrackedProcs();
        return { ok: true, killed: n };
    });

    // --- Session preference (set by greeter's "Always start in this session"
    // checkbox; reset here so the greeter shows on next boot). ------------
    ipcMain.handle('session:reset-greeter-pref', () => {
        if (!IS_LINUX) return { ok: false, error: 'Greeter pref lives on Outlaw OS.' };
        const prefPath = path.join(os.homedir(), '.outlaw-session-pref');
        try {
            // Writing "ask" is more explicit than deleting — the greeter's
            // readPref() handles both, but a present file makes the user's
            // intent obvious if they ever cat it.
            fs.writeFileSync(prefPath, 'ask\n', { mode: 0o600 });
            return { ok: true };
        } catch (err) {
            return { ok: false, error: err.message };
        }
    });

    // --- Session switching (Dev vs Desktop, via the boot greeter) ----------
    // The shell can mark the next X session as "dev" by writing two files in
    // the user's home: ~/.outlaw-session (the choice) and
    // ~/.outlaw-session.honor-once (a one-shot signal to the greeter to skip
    // its prompt). Then we quit so the X session ends — agetty autologin +
    // .bash_profile + .xinitrc bring the user back into outlaw-codemaker.
    ipcMain.handle('session:switch-dev', async () => {
        if (!IS_LINUX) {
            return { ok: false, error: 'Session switching runs on Outlaw OS.' };
        }
        try {
            const home = os.homedir();
            fs.writeFileSync(path.join(home, '.outlaw-session'), 'dev\n', { mode: 0o600 });
            fs.writeFileSync(path.join(home, '.outlaw-session.honor-once'), '', { mode: 0o600 });
        } catch (err) {
            return { ok: false, error: err.message };
        }
        // Give the renderer a beat to render the "switching…" toast before we
        // tear down the window.
        setTimeout(() => {
            if (mainWindow && !mainWindow.isDestroyed()) mainWindow.close();
            app.quit();
        }, 350);
        return { ok: true };
    });

    // Is the Dev session actually runnable here? It is iff a Python interpreter
    // (the CodeMaker venv first, else system python3) can import PyQt6 — i.e.
    // CodeMaker will start instead of crashing. Mirrors /usr/local/bin/
    // outlaw-codemaker. On the live ISO this is false until outlaw-setup-dev
    // builds the venv. Lets the UI offer to download the dev env before a switch.
    ipcMain.handle('session:dev-status', async () => {
        if (!IS_LINUX) return { ready: false, reason: 'not-linux' };
        const probe = 'p=/opt/outlaw-codemaker/.venv/bin/python; [ -x "$p" ] || p="$(command -v python3)"; '
            + '{ [ -n "$p" ] && "$p" -c "import PyQt6.QtCore" >/dev/null 2>&1 && echo READY; } || echo NOPE';
        const r = await runShell(probe, { timeout: 8000 });
        return { ready: /READY/.test(r.stdout || '') };
    });

    // Download + build the Dev environment on demand (live ISO / repair). It's
    // long and network-heavy, so run it in a visible terminal (outlaw-term
    // focuses it + holds it open) rather than silently in the background.
    ipcMain.handle('session:setup-dev', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Runs on Outlaw OS.' };
        launchDetached('outlaw-term', ['Set up Dev session', 'outlaw-setup-dev'], { focus: false });
        return { ok: true };
    });
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------
function createWindow() {
    // Fill the WHOLE screen. The Outlaw session runs with no window manager, so
    // a plain `fullscreen: true` request is often ignored and the window opens
    // as a small box in the centre (the symptom users hit on real hardware).
    // Sizing the frameless window to the primary display's exact resolution
    // fills the screen WITH or WITHOUT a WM, and fitToScreen() re-applies it on
    // any display-size change (e.g. a VM window being resized). Off-Linux (dev
    // preview on Windows/macOS) we keep a normal resizable window.
    const wa = screen.getPrimaryDisplay().workArea;
    mainWindow = new BrowserWindow({
        // Frameless backdrop sized to the work area (leaves room for the
        // taskbar). skipTaskbar so the desktop shell doesn't list itself in the
        // taskbar it sits behind. With a WM, app windows float above this;
        // without one, it's the same full-screen kiosk as before.
        ...(IS_LINUX
            ? { x: wa.x, y: wa.y, width: wa.width, height: wa.height, frame: false, skipTaskbar: true }
            : { width: 1280, height: 820 }),
        backgroundColor: '#050505',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
            spellcheck: false,
        },
    });
    mainWindow.setMenu(null);
    if (IS_LINUX) fitToScreen(mainWindow);
    mainWindow.loadFile('index.html');

    // Open all external links in the system browser, never inside the shell.
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        if (/^https?:\/\//i.test(url)) shell.openExternal(url);
        return { action: 'deny' };
    });
    mainWindow.webContents.on('will-navigate', (e, url) => {
        if (url !== mainWindow.webContents.getURL()) { e.preventDefault(); if (/^https?:/i.test(url)) shell.openExternal(url); }
    });
}

// ---------------------------------------------------------------------------
// Background update check (Windows-style: quietly poll, notify on new release)
// ---------------------------------------------------------------------------
async function backgroundUpdateCheck() {
    if (!settings.autoCheck || !settings.updateRepo) return;
    try {
        const info = await updater.checkShellUpdate({ repo: settings.updateRepo, currentVersion: APP_VERSION, channel: settings.updateChannel || 'stable' });
        settings = saveSettings({ ...settings, lastUpdateCheck: Date.now() });
        if (info.available && info.remoteVersion !== settings.lastNotifiedVersion) {
            settings = saveSettings({ ...settings, lastNotifiedVersion: info.remoteVersion });
            sendToast(`Update available: v${info.remoteVersion} — open Settings to install.`);
        }
    } catch (e) {
        // Silent in the background; manual checks surface the error.
        console.warn('Background update check failed:', e.message);
    }
}

function startAutoCheck() {
    if (autoCheckTimer) clearInterval(autoCheckTimer);
    if (!settings.autoCheck) return;
    setTimeout(backgroundUpdateCheck, 30 * 1000);        // first check ~30s after boot
    autoCheckTimer = setInterval(backgroundUpdateCheck, 6 * 60 * 60 * 1000); // every 6h
}

app.whenReady().then(() => {
    registerIpc();
    // Phase 14h — make sure the greeter can already see the current theme on the
    // very next boot, even for users who set it before this feature existed (they
    // wouldn't have re-saved settings). Cheap one-shot write.
    mirrorThemeToHome(settings && settings.theme);
    createWindow();
    startAutoCheck();
    // SC7 — start the VRAM tier background poll now that a renderer exists.
    // 10s cadence; uses .unref() inside the monitor so it never holds the
    // event loop open during shutdown.
    vramTier.startBackgroundPoll();
    app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
