// ============================================================================
// Outlaw OS - Secure preload bridge
// ----------------------------------------------------------------------------
// Runs in an isolated world with Node access. Exposes ONLY an explicit, audited
// API to the renderer via contextBridge. The renderer never gets `require`,
// `ipcRenderer`, or raw shell access. Everything is funnelled through named IPC
// channels that are validated in the main process.
// ============================================================================
const { contextBridge, ipcRenderer } = require('electron');

// Whitelisted one-way event channels the renderer may subscribe to.
const EVENT_CHANNELS = ['ai-stream', 'system-tick', 'toast',
    'diagnostics-progress',
    'vram-tier-changed',  // SC7 — fires on tier transitions only
];

contextBridge.exposeInMainWorld('outlaw', {
    // --- System information -------------------------------------------------
    system: {
        info: () => ipcRenderer.invoke('system:info'),
        stats: () => ipcRenderer.invoke('system:stats'),
        processes: () => ipcRenderer.invoke('system:processes'),
        gpu: () => ipcRenderer.invoke('system:gpu'),
        // SC2 System Core readouts. Renderer calls these on a 2s timer that
        // only ticks while the System Core screen is visible.
        gpuDetailed: () => ipcRenderer.invoke('system:gpu-detailed'),
        disk: () => ipcRenderer.invoke('system:disk'),
        net: () => ipcRenderer.invoke('system:net'),
        inventory: () => ipcRenderer.invoke('system:inventory'),
        // Returns {live: bool, dismissed: bool}. Drives the live-ISO welcome
        // card on the Dashboard.
        liveIso: () => ipcRenderer.invoke('system:live-iso'),
    },

    // --- Files (read-only browsing + guarded open) --------------------------
    files: {
        list: (dir) => ipcRenderer.invoke('files:list', dir),
        open: (target) => ipcRenderer.invoke('files:open', target),
        home: () => ipcRenderer.invoke('files:home'),
    },

    // --- Applications (allowlisted launchers + on-demand installer) --------
    apps: {
        launch: (id) => ipcRenderer.invoke('apps:launch', id),
        list: () => ipcRenderer.invoke('apps:list'),
        // Curated catalog of optional installable apps.
        catalog: () => ipcRenderer.invoke('apps:catalog'),
        // Per-id installed booleans (checked via pacman -Q).
        installedList: () => ipcRenderer.invoke('apps:installed-list'),
        // Install / uninstall via pkexec pacman. Renderer only sends `id` —
        // the main process resolves it against the catalog allowlist.
        install: (id) => ipcRenderer.invoke('apps:install', id),
        uninstall: (id) => ipcRenderer.invoke('apps:uninstall', id),
        // Refresh local pacman DB. Useful before an install if the DB is stale.
        refreshDb: () => ipcRenderer.invoke('apps:refresh-db'),
    },

    // --- Terminal (guarded executor) ----------------------------------------
    terminal: {
        // opts: { confirmDangerous: boolean }
        run: (command, opts) => ipcRenderer.invoke('terminal:run', { command, opts }),
        inspect: (command) => ipcRenderer.invoke('terminal:inspect', command),
    },

    // --- Persistent settings ------------------------------------------------
    settings: {
        get: () => ipcRenderer.invoke('settings:get'),
        set: (patch) => ipcRenderer.invoke('settings:set', patch),
    },

    // --- Local AI assistant -------------------------------------------------
    ai: {
        status: () => ipcRenderer.invoke('ai:status'),
        enable: () => ipcRenderer.invoke('ai:enable'),
        disable: () => ipcRenderer.invoke('ai:disable'),
        ask: (prompt) => ipcRenderer.invoke('ai:ask', prompt),
        // Run a tool action the AI proposed, after the user approved it.
        confirmAction: (action) => ipcRenderer.invoke('ai:confirm-action', action),
    },

    // --- Gaming -------------------------------------------------------------
    gaming: {
        status: () => ipcRenderer.invoke('gaming:status'),
        setPerformance: (on) => ipcRenderer.invoke('gaming:performance', on),
    },

    // --- Power / boot management -------------------------------------------
    power: {
        hotswap: () => ipcRenderer.invoke('power:hotswap'),
        bootTargets: () => ipcRenderer.invoke('power:boot-targets'),
        reboot: () => ipcRenderer.invoke('power:reboot'),
        shutdown: () => ipcRenderer.invoke('power:shutdown'),
    },

    // --- Updates / installer -----------------------------------------------
    updates: {
        check: () => ipcRenderer.invoke('updates:check'),
        apply: () => ipcRenderer.invoke('updates:apply'),
        checkShell: () => ipcRenderer.invoke('updates:check-shell'),
        installShell: (info) => ipcRenderer.invoke('updates:install-shell', info),
        // Rollback to /usr/share/outlaw-os.prev (kept by outlaw-update-apply).
        // checkRollback returns { available: bool } so the button can stay
        // disabled until there's actually something to roll back to.
        checkRollback: () => ipcRenderer.invoke('updates:rollback-check'),
        rollback: () => ipcRenderer.invoke('updates:rollback'),
    },
    installer: {
        launch: () => ipcRenderer.invoke('installer:launch'),
    },

    // --- Session (Dev vs Desktop via the boot greeter) ---------------------
    session: {
        // Mark the next X session as Dev (Outlaw CodeMaker), bypassing the
        // greeter once, and end this X session so the switch can happen.
        switchToDev: () => ipcRenderer.invoke('session:switch-dev'),
        // Reset the persistent greeter preference back to "ask" so the
        // session greeter shows again on the next boot. Useful when the user
        // ticked "Always start in this session" and now wants to flip.
        resetGreeterPref: () => ipcRenderer.invoke('session:reset-greeter-pref'),
    },

    // --- SC3 System Core diagnostics ---------------------------------------
    // Streaming progress arrives via outlaw.on('diagnostics-progress', cb).
    diagnostics: {
        run: (profile) => ipcRenderer.invoke('diagnostics:run', profile),
        cancel: () => ipcRenderer.invoke('diagnostics:cancel'),
        status: () => ipcRenderer.invoke('diagnostics:status'),
        listReports: () => ipcRenderer.invoke('diagnostics:list-reports'),
        readReport: (filename) => ipcRenderer.invoke('diagnostics:read-report', filename),
    },

    // --- SC5 Cold-mode TTS -------------------------------------------------
    // CPU-only TTS via piper / espeak-ng. status() reports the active engine,
    // whether the toggle is on, and a hint if neither is installed. speak()
    // is a no-op (returns ok:true did:'muted') when the toggle is off — that
    // way the renderer can fire-and-forget without checking the state first.
    tts: {
        status: (opts) => ipcRenderer.invoke('tts:status', opts),
        speak: (text) => ipcRenderer.invoke('tts:speak', text),
        cancel: () => ipcRenderer.invoke('tts:cancel'),
    },

    // --- SC4 Scheduled diagnostic checks -----------------------------------
    // Wraps systemctl --user for the three outlaw-diagnose-*.timer units.
    // status() returns { daily, weekly, monthly } objects with enabled,
    // active, nextRun, lastRun, lastResult. enable/disable/runNow take the
    // schedule id ('daily' | 'weekly' | 'monthly').
    scheduled: {
        status: () => ipcRenderer.invoke('scheduled:status'),
        enable: (id) => ipcRenderer.invoke('scheduled:enable', id),
        disable: (id) => ipcRenderer.invoke('scheduled:disable', id),
        runNow: (id) => ipcRenderer.invoke('scheduled:run-now', id),
    },

    // --- SC7 VRAM tier (Aggressive VRAM saver) -----------------------------
    // status({force:true}) skips the 5s probe cache. Renderer subscribes to
    // tier transitions via outlaw.on('vram-tier-changed', cb). setMode takes
    // 'auto' | 'off' | 'lean' | 'minimal' and persists the choice.
    vram: {
        status: (opts) => ipcRenderer.invoke('vram:status', opts),
        setMode: (mode) => ipcRenderer.invoke('vram:set-mode', mode),
    },

    // --- SC6 System Core Live AI -------------------------------------------
    // Routes prompts through LM Studio with the OUTLAW CORE persona + tight
    // tool allowlist (answer / run_diagnostic / launch_app / notify). ask()
    // returns { ok, intent: {tool, arg, text}, did, detail }. did echoes back
    // which tool actually executed so the renderer can log it.
    coreai: {
        status: () => ipcRenderer.invoke('coreai:status'),
        ask: (payload) => ipcRenderer.invoke('coreai:ask', payload),
        reset: (sessionId) => ipcRenderer.invoke('coreai:reset', sessionId),
    },

    // --- Safe mode + emergency stop ----------------------------------------
    safeMode: {
        // Returns { active: bool, reason: string }. Consuming the marker also
        // deletes it — banner is one-shot per X session.
        check: () => ipcRenderer.invoke('safe-mode:check'),
    },
    emergency: {
        // Ctrl+Alt+K — hard-kill every subprocess the main process spawned.
        // Last-resort escape hatch for hung pacman, runaway terminal, etc.
        stop: () => ipcRenderer.invoke('emergency:stop'),
    },

    // --- One-way events from main -> renderer -------------------------------
    on: (channel, listener) => {
        if (!EVENT_CHANNELS.includes(channel)) return () => {};
        const wrapped = (_event, ...args) => listener(...args);
        ipcRenderer.on(channel, wrapped);
        return () => ipcRenderer.removeListener(channel, wrapped);
    },
});
