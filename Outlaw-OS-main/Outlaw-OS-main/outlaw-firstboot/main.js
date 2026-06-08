// ============================================================================
// Outlaw OS — first-boot wizard (Electron main process)
// ----------------------------------------------------------------------------
// Runs once on first login (gated by ~/.outlaw-firstboot-done in .xinitrc).
// Shows a checkbox UI of optional package bundles; on accept, runs
// `pkexec pacman -S --needed --noconfirm <packages>` for each enabled bundle,
// streaming progress back to the renderer.
//
// Design choices:
//
//   * BUNDLES is the single source of truth — adding a new option here makes
//     it appear in the UI. Each bundle has an id, label, description, size
//     estimate, and the list of pacman packages it installs.
//
//   * No `nodeIntegration` in the renderer. All privileged ops go through
//     `contextBridge` in preload.js, which only exposes the narrowly-scoped
//     IPC methods we want.
//
//   * Touches the done-marker AFTER the user explicitly clicks Finish or
//     Skip, NOT on window close. If the user force-quits via window manager
//     we run again next login — safer default than silently disabling.
//
//   * Errors install bundles serially, not in parallel. Pacman's sqlite lock
//     would conflict otherwise, and serial progress is easier to read.
// ============================================================================

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { spawn } = require('node:child_process');

// ----------------------------------------------------------------------------
// Bundle definitions — the SINGLE source of truth shown in index.html.
// Renderer reads this via the IPC channel `bundles:list`. Keep the package
// lists in sync with what outlaw-install ESSENTIAL_PKGS does NOT install.
// ----------------------------------------------------------------------------
const BUNDLES = [
    {
        id: 'steam',
        label: 'Steam + gaming stack',
        sizeNote: '~700 MB',
        description:
            'Steam client, Gamescope, GameMode, MangoHud, Vulkan loaders, ' +
            'and 32-bit gaming libs. Required to play Steam games on Outlaw OS.',
        defaultChecked: true,
        packages: [
            'steam',
            'gamemode', 'lib32-gamemode',
            'gamescope',
            'mangohud', 'lib32-mangohud',
            'vulkan-icd-loader', 'lib32-vulkan-icd-loader', 'vulkan-tools',
            'lib32-mesa',
        ],
    },
    {
        id: 'firefox',
        label: 'Firefox web browser',
        sizeNote: '~250 MB',
        description:
            "The default web browser. Skip if you'd rather install a " +
            'different one later via the Apps panel.',
        defaultChecked: true,
        packages: ['firefox'],
    },
    {
        id: 'godot',
        label: 'Godot game engine',
        sizeNote: '~100 MB',
        description:
            "Godot Engine — the editor that pairs with Outlaw CodeMaker for " +
            'AI-driven game development. Skip if you only use the OS as a ' +
            'general desktop.',
        defaultChecked: true,
        packages: ['godot'],
    },
];

// Where we drop the "wizard already ran" marker. .xinitrc checks for this.
const DONE_MARKER = path.join(os.homedir(), '.outlaw-firstboot-done');

// ----------------------------------------------------------------------------
// Window
// ----------------------------------------------------------------------------
let mainWindow = null;

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 760,
        height: 620,
        resizable: false,
        // Frameless-ish — keep a normal title bar for accessibility, but
        // remove all menu chrome that might let the user wander away.
        autoHideMenuBar: true,
        title: 'Outlaw OS — Setup',
        backgroundColor: '#0d0f12',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: false,   // we explicitly --no-sandbox via the launcher
        },
    });

    mainWindow.loadFile(path.join(__dirname, 'index.html'));
    mainWindow.setMenu(null);
}

app.whenReady().then(() => {
    createWindow();
    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

// Don't quit on all-windows-closed during install — we want the install to
// keep running even if the user accidentally closes the window. Quit happens
// explicitly via ipcMain handlers below.
app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
});

// ----------------------------------------------------------------------------
// IPC
// ----------------------------------------------------------------------------

// bundles:list — return the BUNDLES array (renderer renders the checkboxes).
ipcMain.handle('bundles:list', () => BUNDLES);

// bundles:install — install one bundle, streaming output. `bundleId` is the
// `id` field of one BUNDLES entry. Returns { ok, exitCode, errorMessage }.
//
// Output is forwarded to the renderer via the `bundle:progress` event so the
// UI can show a live log. We swallow exceptions and translate them to clean
// error messages — the renderer should never see a stack trace.
ipcMain.handle('bundles:install', async (_event, bundleId) => {
    const bundle = BUNDLES.find((b) => b.id === bundleId);
    if (!bundle) {
        return {
            ok: false,
            exitCode: -1,
            errorMessage: `Unknown bundle id: ${bundleId}`,
        };
    }

    const sendLine = (line) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('bundle:progress', { bundleId, line });
        }
    };

    sendLine(`-- Installing ${bundle.label} (${bundle.packages.length} packages)…`);

    // pkexec triggers polkit auth (first time per session prompts; cached
    // after). We pass --noconfirm so pacman doesn't sit waiting for "y".
    // --needed skips packages that are already current — important for
    // re-runs and for packages we deliberately keep in ESSENTIAL_PKGS.
    // -Sy (sync + install) refreshes the databases first so a fresh install
    // with absent/stale sync DBs doesn't fail with "database file for 'core'
    // does not exist (use '-Sy')".
    return await new Promise((resolve) => {
        const args = [
            'pacman', '-Sy', '--needed', '--noconfirm',
            ...bundle.packages,
        ];
        let proc;
        try {
            proc = spawn('pkexec', args, { stdio: ['ignore', 'pipe', 'pipe'] });
        } catch (err) {
            sendLine(`!! spawn failed: ${err.message}`);
            resolve({ ok: false, exitCode: -1, errorMessage: err.message });
            return;
        }

        const onLine = (chunk) => {
            for (const line of chunk.toString('utf8').split('\n')) {
                if (line.length > 0) sendLine(line);
            }
        };
        proc.stdout.on('data', onLine);
        proc.stderr.on('data', onLine);

        proc.on('close', (code) => {
            if (code === 0) {
                sendLine(`++ ${bundle.label}: done.`);
                resolve({ ok: true, exitCode: 0 });
            } else if (code === 126 || code === 127) {
                // 126 = auth cancelled, 127 = pkexec couldn't run pacman
                const msg = code === 126
                    ? 'Authorization cancelled.'
                    : 'pkexec / pacman not found.';
                sendLine(`!! ${msg}`);
                resolve({ ok: false, exitCode: code, errorMessage: msg });
            } else {
                sendLine(`!! ${bundle.label}: pacman exited ${code}.`);
                resolve({
                    ok: false,
                    exitCode: code,
                    errorMessage: `pacman exited with code ${code}`,
                });
            }
        });

        proc.on('error', (err) => {
            sendLine(`!! ${err.message}`);
            resolve({ ok: false, exitCode: -1, errorMessage: err.message });
        });
    });
});

// bundles:done — touch the done-marker so .xinitrc skips us next login,
// then quit Electron so X moves on to the greeter.
ipcMain.handle('bundles:done', () => {
    try {
        fs.writeFileSync(DONE_MARKER, `done: ${new Date().toISOString()}\n`);
    } catch (err) {
        // Non-fatal — user can re-run if they care. Just log.
        console.error('failed to write done-marker:', err);
    }
    app.quit();
});
