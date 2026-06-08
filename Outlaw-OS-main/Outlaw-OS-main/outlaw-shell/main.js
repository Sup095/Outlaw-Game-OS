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

const { app, BrowserWindow, ipcMain, shell } = require('electron');
const { spawn, execFile } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const aiAgent = require('./ai-agent');
const updater = require('./updater');
const APP_VERSION = require('./package.json').version;
const { DiagnosticRunner, listReports: listDiagReports, readReport: readDiagReport } = require('./diagnostics');
const tts = require('./tts');
const { VramTierMonitor } = require('./vram-tier');
const coreai = require('./coreai');

const IS_LINUX = process.platform === 'linux';
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
    aiEnabled: false,        // AI is OFF by default (low-VRAM friendly first boot)
    // 'local-model' is LM Studio's sentinel — it routes to whatever model the
    // user has loaded in LM Studio's UI. Matches Outlaw CodeMaker's default.
    aiModel: 'local-model',
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
};

function loadSettings() {
    try {
        const raw = fs.readFileSync(SETTINGS_PATH, 'utf8');
        return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
    } catch {
        return { ...DEFAULT_SETTINGS };
    }
}

function saveSettings(s) {
    try {
        fs.mkdirSync(path.dirname(SETTINGS_PATH), { recursive: true });
        fs.writeFileSync(SETTINGS_PATH, JSON.stringify(s, null, 2));
    } catch (e) {
        console.error('Could not persist settings:', e.message);
    }
    return s;
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

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------
function registerIpc() {
    ipcMain.handle('system:info', () => systemInfo());

    ipcMain.handle('system:stats', () => {
        const mem = memInfo();
        return { cpu: cpuPercent(), ramPct: mem.totalKb ? (mem.usedKb / mem.totalKb) * 100 : 0,
                 ramUsed: fmtGb(mem.usedKb), ramTotal: fmtGb(mem.totalKb), time: new Date().toLocaleTimeString() };
    });

    ipcMain.handle('system:processes', async () => {
        if (!IS_LINUX) return [{ pid: process.pid, comm: 'electron', cpu: '0.0', mem: '0.0' }];
        const r = await runShell('ps -eo pid,comm,pcpu,pmem --sort=-pcpu | head -n 30');
        return r.stdout.split('\n').slice(1).map((l) => {
            const m = l.trim().match(/^(\d+)\s+(.+?)\s+([\d.]+)\s+([\d.]+)$/);
            return m ? { pid: m[1], comm: m[2], cpu: m[3], mem: m[4] } : null;
        }).filter(Boolean);
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
        const lm = await coreai.status();
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
            model: settings.aiModel || 'local-model',
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

    ipcMain.handle('files:home', () => os.homedir());
    ipcMain.handle('files:list', (_e, dir) => listFiles(dir || os.homedir()));
    ipcMain.handle('files:open', (_e, target) => openPath(target));

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

    ipcMain.handle('apps:install', async (_e, id) => {
        if (!IS_LINUX) return { ok: false, error: 'Install runs on Outlaw OS.' };
        const app = APP_CATALOG.find((a) => a.id === id);
        if (!app) return { ok: false, error: 'Unknown app id.' };
        // pkexec prompts for the user's password via polkit; --needed avoids
        // re-installing already-current packages; --noconfirm skips y/n prompts.
        // -Sy (sync + install) refreshes the package databases first — REQUIRED
        // on the live ISO and a fresh install, where the sync DBs are absent
        // ("error: database file for 'core' does not exist (use '-Sy')"). It's
        // the partial-upgrade pattern, but acceptable for installing a single
        // curated leaf package; the system updater does full -Syu upgrades.
        // 20-minute timeout — large multilib packages on slow connections.
        const cmd = `pkexec pacman -Sy --needed --noconfirm ${app.pkg}`;
        const r = await runShell(cmd, { timeout: 1000 * 60 * 20 });
        const tail = (r.stdout || r.stderr || `exit ${r.code}`).slice(-3000);
        if (r.code !== 0) {
            // Surface a useful hint for the most common failure modes.
            const hint = /database file for|use ['"]?-Sy/i.test(tail)
                ? '\n\nHint: the package database could not be refreshed. Check your network connection and try again.'
                : /not found in AUR|target not found/i.test(tail)
                    ? '\n\nHint: that package name was not found. Your mirror list or database may need attention.'
                    : /not authorized|authentication agent|polkit/i.test(tail)
                        ? '\n\nHint: no authorization agent answered. (On the installed system this is handled automatically.)'
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
        const s = await aiAgent.status();
        return { ...s, enabled: settings.aiEnabled, model: settings.aiModel };
    });

    ipcMain.handle('ai:enable', async () => {
        // LM Studio is a user-launched desktop app, not a systemd service we own.
        // Enabling here means "the shell will route prompts to it"; the actual
        // server is started by the user in LM Studio's UI ("Start Server"). We
        // try launching the LM Studio helper as a convenience, but don't gate
        // success on it — the user may already have LM Studio running.
        settings = saveSettings({ ...settings, aiEnabled: true });
        if (IS_LINUX) {
            // Fire-and-forget — never block the IPC reply on it.
            runShell('outlaw-lm-studio 2>/dev/null &', { timeout: 2000 }).catch(() => {});
        }
        const s = await aiAgent.status();
        return { ok: true, enabled: true, available: s.available, model: settings.aiModel };
    });

    ipcMain.handle('ai:disable', async () => {
        // Just flips the routing bit — we don't kill LM Studio, the user owns it.
        settings = saveSettings({ ...settings, aiEnabled: false });
        return { ok: true, enabled: false };
    });

    ipcMain.handle('ai:ask', async (_e, prompt) => {
        if (!settings.aiEnabled) return { error: 'AI is disabled. Enable it in Settings.' };
        const s = await aiAgent.status();
        if (!s.available) {
            return {
                error: 'LM Studio isn\'t reachable. Open LM Studio, load a model, then click "Start Server" (port 1234).',
            };
        }
        try {
            const appIds = Object.keys(APP_REGISTRY);
            const intent = await aiAgent.ask(prompt, { model: settings.aiModel, appIds });
            return await executeIntent(intent);
        } catch (e) {
            return { error: e.message };
        }
    });

    ipcMain.handle('ai:confirm-action', async (_e, action) => {
        if (action && action.tool === 'run_command') {
            const r = await runShell(action.arg, { timeout: 120000 });
            return { text: (r.stdout || r.stderr || `(exit ${r.code})`).slice(0, 4000), did: 'run_command' };
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
            await runShell(`pkexec outlaw-perf ${on ? 'performance' : 'schedutil'} 2>/dev/null || true`, { timeout: 8000 });
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
        const r = await runShell('checkupdates 2>/dev/null | wc -l');
        return { updates: parseInt(r.stdout || '0', 10) || 0 };
    });
    ipcMain.handle('updates:apply', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Updates run on Outlaw OS.' };
        // pkexec prompts for the operator password (no passwordless root).
        const r = await runShell('pkexec pacman -Syu --noconfirm', { timeout: 1000 * 60 * 20 });
        return { ok: r.code === 0, log: (r.stdout || r.stderr).slice(-4000) };
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
            const cmd = `pkexec outlaw-update-apply ${JSON.stringify(tarPath)} ${JSON.stringify(sha)}`;
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
        const r = await runShell('pkexec outlaw-update-rollback', { timeout: 1000 * 60 * 2 });
        if (r.code !== 0) return { ok: false, error: (r.stderr || r.stdout || `exit ${r.code}`).slice(-2000) };
        return { ok: true, log: (r.stdout || '').slice(-2000), restart: true };
    });
    ipcMain.handle('installer:launch', async () => {
        if (!IS_LINUX) return { ok: false, error: 'Installer runs from the live boot media.' };
        // outlaw-term opens the installer in a titled terminal AND pulls
        // keyboard focus to it (the desktop has no window manager, so otherwise
        // the window appears but won't accept typing). focus:false because
        // outlaw-term handles focus itself.
        launchDetached('outlaw-term', ['Outlaw OS Installer', 'outlaw-install'], { focus: false });
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
        const r = await runShell('pkexec outlaw-tune apply', { timeout: 1000 * 60 * 2 });
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
        const r = await runShell('pkexec outlaw-tune reset', { timeout: 1000 * 60 });
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
    mainWindow = new BrowserWindow({
        fullscreen: true,
        frame: false,
        kiosk: IS_LINUX,            // kiosk on the real OS; windowed for desktop preview
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
    createWindow();
    startAutoCheck();
    // SC7 — start the VRAM tier background poll now that a renderer exists.
    // 10s cadence; uses .unref() inside the monitor so it never holds the
    // event loop open during shutdown.
    vramTier.startBackgroundPoll();
    app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
