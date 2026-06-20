// ============================================================================
// Outlaw OS — Session greeter (Electron main)
// ----------------------------------------------------------------------------
// Tiny kiosk window that asks "Dev session?" vs "Desktop?" on every boot.
// Writes the choice to ~/.outlaw-session as plain text:
//
//     dev      → .xinitrc execs outlaw-codemaker
//     desktop  → .xinitrc execs the outlaw-shell Electron app
//     tty      → .xinitrc exits X and drops the user to a TTY
//
// Hardened the same way as the main shell: contextIsolation on, sandboxed
// renderer, no node integration, validated IPC.
// ============================================================================

const { app, BrowserWindow, ipcMain, screen } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { exec } = require('child_process');

// Tiny read-only shell helper (fixed commands, no user input → no injection).
// Always resolves (never rejects) so a missing tool can't break the greeter.
function sh(cmd, ms = 4000) {
    return new Promise((resolve) => {
        try { exec(cmd, { timeout: ms, windowsHide: true }, (_e, out) => resolve((out || '').trim())); }
        catch { resolve(''); }
    });
}

const SESSION_PATH = path.join(os.homedir(), '.outlaw-session');
// One-shot bypass — created by the desktop shell's "Switch to Dev session"
// button so the user doesn't have to re-click Dev at the greeter on the way
// over. The greeter deletes it after honoring.
const HONOR_ONCE_PATH = path.join(os.homedir(), '.outlaw-session.honor-once');
// Persistent preference — set when the user ticks "Always start in this
// session" in the greeter. Values: "ask" (default — show greeter every boot),
// "dev", "desktop", or "tty" (skip greeter, always go straight to that).
// Reset to "ask" via Desktop Settings → Session → Show greeter on next boot.
const PREF_PATH = path.join(os.homedir(), '.outlaw-session-pref');
let mainWindow = null;
let chosen = null;

function readPref() {
    try {
        const raw = fs.readFileSync(PREF_PATH, 'utf8').trim().toLowerCase();
        if (['ask', 'dev', 'desktop', 'tty'].includes(raw)) return raw;
    } catch { /* file absent — fall back */ }
    return 'ask';
}

function writePref(value) {
    try {
        fs.writeFileSync(PREF_PATH, value + '\n', { mode: 0o600 });
    } catch (err) {
        console.error('outlaw-greeter: could not write pref file:', err.message);
    }
}

// Phase 14h — the desktop shell mirrors its chosen visual theme to
// ~/.outlaw-theme ('green' | 'gold' | 'broken'). Read it so the session chooser
// matches the user's system look instead of a fixed gold-on-gunmetal palette.
// Defaults to 'green' (the shell default) when absent/unreadable.
const THEME_PATH = path.join(os.homedir(), '.outlaw-theme');
const THEME_BG = { green: '#050705', gold: '#0b0e14', broken: '#060806' };
function readTheme() {
    try {
        const raw = fs.readFileSync(THEME_PATH, 'utf8').trim().toLowerCase();
        if (['green', 'gold', 'broken'].includes(raw)) return raw;
    } catch { /* absent — default below */ }
    return 'green';
}

function persistChoice(choice) {
    if (chosen) return;  // first call wins
    chosen = choice;
    try {
        fs.writeFileSync(SESSION_PATH, choice + '\n', { mode: 0o600 });
    } catch (err) {
        console.error('outlaw-greeter: could not write session file:', err.message);
    }
}

function createWindow() {
    const theme = readTheme();
    const display = screen.getPrimaryDisplay();
    const { width, height } = display.workAreaSize;
    mainWindow = new BrowserWindow({
        width: Math.min(720, width),
        height: Math.min(440, height),
        frame: false,
        resizable: false,
        movable: false,
        fullscreen: false,
        kiosk: false,             // a small centered card, not a fullscreen takeover
        backgroundColor: THEME_BG[theme] || THEME_BG.green,
        alwaysOnTop: true,
        show: false,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
            spellcheck: false,
        },
    });
    mainWindow.setMenu(null);
    // Pass the theme in via the file query so the renderer can apply it before
    // the chooser is revealed (the green boot intro covers it until then → no flash).
    mainWindow.loadFile('index.html', { query: { theme } });
    mainWindow.once('ready-to-show', () => {
        mainWindow.center();
        mainWindow.show();
        mainWindow.focus();
    });
}

ipcMain.handle('greeter:choose', (_e, choice, remember) => {
    const valid = ['dev', 'desktop', 'tty'];
    if (!valid.includes(choice)) return { ok: false };
    persistChoice(choice);
    if (remember === true) {
        // User asked to skip the greeter on future boots. Mirror the choice
        // into the persistent pref file. They can reset via Desktop Settings.
        writePref(choice);
    }
    // Close the window; .xinitrc will then read the session file and exec the
    // chosen app. Use a small delay so the click animation can render.
    setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.close();
        app.quit();
    }, 120);
    return { ok: true };
});

// Phase 8: real boot messages for the cinematic boot intro (read-only).
ipcMain.handle('greeter:boot-log', async () => {
    let out = await sh('journalctl -b --no-pager -o cat -n 12 2>/dev/null');
    if (!out) out = await sh('dmesg 2>/dev/null | tail -n 12');
    return out ? out.split('\n').map((l) => l.slice(0, 84)).filter(Boolean) : [];
});

// Phase 8: optional pre-flight check shown at the greeter before entering a
// session. All read-only; never blocks the chooser.
ipcMain.handle('greeter:preflight', async () => {
    const cpu = (os.cpus()[0] || {}).model || 'CPU';
    const cores = os.cpus().length;
    const ramGb = Math.round((os.totalmem() / (1024 ** 3)) * 10) / 10;
    let gpu = await sh("lspci 2>/dev/null | grep -Ei 'vga|3d|display' | sed 's/^.*: //' | head -n1");
    if (!gpu) gpu = 'unknown';
    const nv = await sh('nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1');
    const vramGb = nv && Number(nv) ? Math.round((Number(nv) / 1024) * 10) / 10 : 0;
    const diskFree = await sh("df -h --output=avail / 2>/dev/null | tail -n1");
    let safeGfx = false;
    try { safeGfx = fs.existsSync(path.join(os.homedir(), '.outlaw-safe-gfx')); } catch {}
    const warnings = [];
    if (ramGb && ramGb < 4) warnings.push('Low RAM (under 4 GB) — pick a tiny AI model and the Desktop session.');
    if (safeGfx) warnings.push('Safe graphics is active (software rendering). Delete ~/.outlaw-safe-gfx to retry your GPU.');
    return { ok: true, cpu, cores, ramGb, gpu, vramGb, diskFree: (diskFree || '').trim(), safeGfx, warnings };
});

app.whenReady().then(() => {
    // If the desktop shell asked us to honor the previous choice once,
    // skip the prompt entirely and let .xinitrc read the existing file.
    if (fs.existsSync(HONOR_ONCE_PATH) && fs.existsSync(SESSION_PATH)) {
        try { fs.unlinkSync(HONOR_ONCE_PATH); } catch { /* ignored */ }
        chosen = 'skipped';  // so window-all-closed doesn't overwrite the choice
        app.quit();
        return;
    }
    // Persistent preference: if the user previously ticked "Always start in
    // this session", skip the window entirely and route straight to .xinitrc.
    const pref = readPref();
    if (pref !== 'ask') {
        persistChoice(pref);
        app.quit();
        return;
    }
    createWindow();
});
app.on('window-all-closed', () => {
    // Safety net: if the window was dismissed without a choice (e.g. crash),
    // default to desktop so .xinitrc still has something to read.
    if (!chosen) persistChoice('desktop');
    app.quit();
});
