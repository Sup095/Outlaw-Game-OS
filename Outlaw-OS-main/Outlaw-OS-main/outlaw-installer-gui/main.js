// ============================================================================
// Outlaw OS — graphical installer (Electron main process)
// ----------------------------------------------------------------------------
// A point-and-click front-end over /usr/local/bin/outlaw-install's machine
// modes. ALL disk logic lives in that battle-tested bash script — this app
// only probes (read-only), collects choices in real GUI fields (which also
// sidesteps every terminal echo/focus problem), and streams progress.
//
//   probe   → `outlaw-install --probe-extras`  +  `lsblk --json`
//   install → `outlaw-install --auto-wipe|--auto-free|--auto-shrink …`
//             (password written to stdin line 1, never argv)
//
// Runs as root on the live ISO (the live session is root). On a non-root
// system it refuses with a friendly message instead of half-working.
// ============================================================================

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');
const { spawn, execFile } = require('node:child_process');

const INSTALLER = '/usr/local/bin/outlaw-install';
const IS_LINUX = process.platform === 'linux';

let win = null;
let child = null; // the running install process (only one ever)

function createWindow() {
    win = new BrowserWindow({
        fullscreen: true,
        frame: false,
        backgroundColor: '#050705',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
            spellcheck: false,
        },
    });
    win.setMenu(null);
    win.loadFile('index.html');
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());

// ---------------------------------------------------------------------------
// Probe — read-only hardware survey, merged into one friendly object.
// ---------------------------------------------------------------------------
function run(cmd, args, timeoutMs = 60_000) {
    return new Promise((resolve) => {
        execFile(cmd, args, { timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024 },
            (err, stdout, stderr) => resolve({ err, stdout: stdout || '', stderr: stderr || '' }));
    });
}

ipcMain.handle('inst:env', () => ({
    linux: IS_LINUX,
    root: IS_LINUX ? process.getuid() === 0 : false,
}));

ipcMain.handle('inst:probe', async () => {
    if (!IS_LINUX) return { error: 'The installer only runs on the Outlaw live system.' };
    const [extras, blk] = await Promise.all([
        run(INSTALLER, ['--probe-extras'], 120_000),
        run('lsblk', ['--json', '-b', '-o', 'PATH,NAME,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINTS,PKNAME']),
    ]);
    if (extras.err) return { error: 'Could not inspect the disks: ' + (extras.stderr || extras.err.message) };

    // Parse the line protocol from --probe-extras.
    const disks = new Map();
    let efi = false;
    let liveUsb = '';
    for (const line of extras.stdout.split('\n')) {
        const t = line.trim().split(/\s+/);
        if (!t.length) continue;
        if (t[0] === 'EFI') efi = t[1] === '1';
        else if (t[0] === 'LIVEUSB') liveUsb = (t[1] && t[1] !== '-') ? t[1] : '';
        else if (t[0] === 'DISK') {
            disks.set(t[1], {
                path: t[1],
                sizeMib: parseInt(t[3], 10) || 0,
                model: line.split(/MODEL\s+/)[1]?.trim() || '',
                freeStartMib: 0, freeMib: 0, esp: '', parts: [], shrink: [],
            });
        } else if (t[0] === 'FREE' && disks.has(t[1])) {
            disks.get(t[1]).freeStartMib = parseInt(t[3], 10) || 0;
            disks.get(t[1]).freeMib = parseInt(t[5], 10) || 0;
        } else if (t[0] === 'ESP' && disks.has(t[1])) {
            disks.get(t[1]).esp = (t[2] && t[2] !== '-') ? t[2] : '';
        } else if (t[0] === 'SHRINK') {
            const parent = [...disks.keys()].find((d) => t[1].startsWith(d));
            if (parent) {
                const sizeMib = parseInt(t[5], 10) || 0;
                const minMib = parseInt(t[7], 10) || 0;
                // 1 GiB safety margin, floor to whole GiB.
                const maxTakeGib = Math.max(0, Math.floor((sizeMib - minMib - 1024) / 1024));
                disks.get(parent).shrink.push({ part: t[1], fstype: t[3], sizeMib, minMib, maxTakeGib });
            }
        }
    }
    // Attach partition listings from lsblk (names/labels/sizes for display).
    try {
        const j = JSON.parse(blk.stdout);
        for (const dev of j.blockdevices || []) {
            for (const ch of dev.children || []) {
                const parent = '/dev/' + (ch.pkname || dev.name);
                if (disks.has(parent) && ch.type === 'part') {
                    disks.get(parent).parts.push({
                        path: ch.path,
                        sizeMib: Math.floor((ch.size || 0) / 1024 / 1024),
                        fstype: ch.fstype || '',
                        label: ch.label || '',
                    });
                }
            }
        }
    } catch { /* display-only data — fine without it */ }

    return { efi, liveUsb, disks: [...disks.values()] };
});

// Quick online check so the wizard can warn BEFORE the user fills everything in.
ipcMain.handle('inst:online', async () => {
    const r = await run('curl', ['-fsS', '--max-time', '10', '-o', '/dev/null',
        'https://geo.mirror.pkgbuild.com/lastsync'], 15_000);
    return { online: !r.err };
});

// ---------------------------------------------------------------------------
// Install — spawn the bash engine, stream lines + stage markers to the UI.
// ---------------------------------------------------------------------------
ipcMain.handle('inst:start', async (_e, opts) => {
    if (child) return { ok: false, error: 'An install is already running.' };
    if (!IS_LINUX || process.getuid() !== 0) {
        return { ok: false, error: 'The installer must run from the Outlaw live USB.' };
    }
    const { mode, disk, part, gib, username, password } = opts || {};
    if (!/^[a-z_][a-z0-9_-]*$/.test(username || '')) return { ok: false, error: 'Invalid username.' };
    if (!password) return { ok: false, error: 'Empty password.' };

    let args;
    if (mode === 'wipe')        args = ['--auto-wipe', disk, username];
    else if (mode === 'free')   args = ['--auto-free', disk, String(gib), username];
    else if (mode === 'shrink') args = ['--auto-shrink', part, String(gib), username];
    else return { ok: false, error: 'Unknown install mode.' };

    child = spawn(INSTALLER, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    child.stdin.write(password + '\n');
    child.stdin.end();

    const send = (ch, payload) => { try { win?.webContents.send(ch, payload); } catch {} };
    let sawDone = false, sawError = '';
    let buf = '';
    const onData = (data) => {
        buf += data.toString();
        let i;
        while ((i = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, i); buf = buf.slice(i + 1);
            if (line.startsWith('@@STAGE ')) {
                const rest = line.slice(8);
                const sp = rest.indexOf(' ');
                send('inst:stage', { id: sp > 0 ? rest.slice(0, sp) : rest, label: sp > 0 ? rest.slice(sp + 1) : '' });
            } else if (line.startsWith('@@ERROR ')) {
                sawError = line.slice(8);
                send('inst:line', '! ' + sawError);
            } else if (line.startsWith('@@DONE')) {
                sawDone = true;
            } else if (line.trim()) {
                send('inst:line', line);
            }
        }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('close', (code) => {
        child = null;
        if (sawDone && code === 0) send('inst:done', {});
        else send('inst:error', { message: sawError || `The installer stopped unexpectedly (exit ${code}).` });
    });
    child.on('error', (err) => {
        child = null;
        send('inst:error', { message: 'Could not start the installer: ' + err.message });
    });
    return { ok: true };
});

ipcMain.handle('inst:reboot', () => {
    spawn('systemctl', ['reboot'], { detached: true, stdio: 'ignore' }).unref();
    return { ok: true };
});

ipcMain.handle('inst:quit', () => { app.quit(); return { ok: true }; });
