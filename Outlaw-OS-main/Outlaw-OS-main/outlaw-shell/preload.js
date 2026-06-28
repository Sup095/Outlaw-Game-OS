// ============================================================================
// Outlaw OS - Secure preload bridge
// ----------------------------------------------------------------------------
// Runs in an isolated world with Node access. Exposes ONLY an explicit, audited
// API to the renderer via contextBridge. The renderer never gets `require`,
// `ipcRenderer`, or raw shell access. Everything is funnelled through named IPC
// channels that are validated in the main process.
// ============================================================================
const { contextBridge, ipcRenderer, webFrame } = require('electron');

// Whitelisted one-way event channels the renderer may subscribe to.
const EVENT_CHANNELS = ['ai-stream', 'system-tick', 'toast',
    'diagnostics-progress',
    'vram-tier-changed',  // SC7 — fires on tier transitions only
    'job-progress',       // Phase 12 — live phase/log for the loading screen
];

contextBridge.exposeInMainWorld('outlaw', {
    // --- System information -------------------------------------------------
    system: {
        info: () => ipcRenderer.invoke('system:info'),
        stats: () => ipcRenderer.invoke('system:stats'),
        processes: () => ipcRenderer.invoke('system:processes'),
        // Phase 8: real boot messages for the cinematic boot screen.
        bootLog: () => ipcRenderer.invoke('system:boot-log'),
        // Phase 5: End task / End process tree.
        kill: (pid) => ipcRenderer.invoke('proc:kill', pid),
        killTree: (pid) => ipcRenderer.invoke('proc:kill-tree', pid),
        gpu: () => ipcRenderer.invoke('system:gpu'),
        // SC2 System Core readouts. Renderer calls these on a 2s timer that
        // only ticks while the System Core screen is visible.
        gpuDetailed: () => ipcRenderer.invoke('system:gpu-detailed'),
        disk: () => ipcRenderer.invoke('system:disk'),
        battery: () => ipcRenderer.invoke('system:battery'),
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
        openManager: (dir) => ipcRenderer.invoke('files:open-manager', dir),
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
        // Phase 15c — search ALL official packages + install any by name.
        search: (query) => ipcRenderer.invoke('apps:search', query),
        installPkg: (pkg) => ipcRenderer.invoke('apps:install-pkg', pkg),
        // Refresh local pacman DB. Useful before an install if the DB is stale.
        refreshDb: () => ipcRenderer.invoke('apps:refresh-db'),
        // Phase 2 — apps the user installed themselves (.desktop entries +
        // AppImages), so they appear in the Apps page and launch in one click.
        discover: () => ipcRenderer.invoke('apps:discover'),
        launchDiscovered: (id) => ipcRenderer.invoke('apps:launch-discovered', id),
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

    // QoL — current shell version (for the "Updated to vX.Y.Z" note).
    appVersion: () => ipcRenderer.invoke('app:version'),

    // Round-2 QOL — volume control (PipeWire/Pulse/ALSA, auto-detected in main).
    audio: {
        get: () => ipcRenderer.invoke('audio:get'),
        set: (pct) => ipcRenderer.invoke('audio:set', pct),
        toggleMute: () => ipcRenderer.invoke('audio:toggle-mute'),
    },
    // Round-2 QOL — screenshots (scrot). mode 'region' | 'full'.
    screenshot: (mode) => ipcRenderer.invoke('screenshot:capture', mode),

    // --- Local AI assistant -------------------------------------------------
    ai: {
        status: () => ipcRenderer.invoke('ai:status'),
        enable: () => ipcRenderer.invoke('ai:enable'),
        disable: () => ipcRenderer.invoke('ai:disable'),
        // opts (Phase 15b) optionally carries { history, summary } for chat memory.
        ask: (prompt, opts) => ipcRenderer.invoke('ai:ask', { prompt, ...(opts || {}) }),
        // Run a tool action the AI proposed, after the user approved it.
        confirmAction: (action) => ipcRenderer.invoke('ai:confirm-action', action),
        // Phase 15b — persistent chats: load/save the whole conversation store.
        chats: {
            load: () => ipcRenderer.invoke('ai:chats:load'),
            save: (store) => ipcRenderer.invoke('ai:chats:save', store),
        },
        // Phase 15b (slice 2) — fold older turns into a running summary.
        summarize: (payload) => ipcRenderer.invoke('ai:summarize', payload),
        // Phase 4: spec-aware local-model recommendation for the setup guide.
        // Forward opts ({purpose,tier,spill}) so the dev/desktop + tier choices apply.
        recommend: (opts) => ipcRenderer.invoke('ai:recommend', opts),
        // #2: optional plain-language AI take on the recommendation (best-effort).
        recommendExplain: (opts) => ipcRenderer.invoke('ai:recommend-explain', opts),
        // Phase 4: hardware-aware plain-prose setup chat (walks the user through
        // getting a local model running). payload = { prompt, history? }.
        setupChat: (payload) => ipcRenderer.invoke('ai:setup-chat', payload),
        // Phase 13.2: pull the built-in base model if it's missing (first run).
        ensureBaseModel: () => ipcRenderer.invoke('ai:ensure-base-model'),
    },
    // Phase 16 — Ollama model management (status / list / pull) for running a
    // larger model through Ollama as a full LM Studio replacement.
    ollama: {
        status: () => ipcRenderer.invoke('ollama:status'),
        list: () => ipcRenderer.invoke('ollama:list'),
        pull: (model) => ipcRenderer.invoke('ollama:pull', model),
    },
    // QoL/accessibility — scale the whole UI (text size). Clamped for safety.
    setZoom: (factor) => { try { webFrame.setZoomFactor(Math.max(0.7, Math.min(2, Number(factor) || 1))); } catch {} },
    // F1 — combined error/warning log.
    errorlog: {
        read: () => ipcRenderer.invoke('errorlog:read'),
        collect: () => ipcRenderer.invoke('errorlog:collect'),
        clear: () => ipcRenderer.invoke('errorlog:clear'),
        add: (payload) => ipcRenderer.invoke('errorlog:add', payload),
        issueUrl: () => ipcRenderer.invoke('errorlog:issue-url'),
        // Open the prefilled GitHub issue in the system browser (window.open is
        // blocked in the kiosk). Save the log to a real file in ~/Downloads.
        openIssue: () => ipcRenderer.invoke('errorlog:open-issue'),
        save: () => ipcRenderer.invoke('errorlog:save'),
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
    // C8 — storage-as-memory (swapfile) for low-RAM machines.
    swap: {
        status: () => ipcRenderer.invoke('swap:status'),
        set: (opts) => ipcRenderer.invoke('swap:set', opts),
    },
    // Advisory community-stability signal for the installed version (read-only
    // GitHub reaction tally). The user's own vote is stored in settings.
    stability: {
        tally: () => ipcRenderer.invoke('stability:tally'),
        // Phase 7: a pre-filled GitHub issue URL for a works/broken report.
        reportUrl: (verdict) => ipcRenderer.invoke('stability:report-url', verdict),
    },
    // Phase 9: session graphics/driver profiles (userspace packages only —
    // never kernel/KMS/bootloader). detect/preview read-only; apply/revert privileged.
    drivers: {
        detect: () => ipcRenderer.invoke('drivers:detect'),
        preview: () => ipcRenderer.invoke('drivers:preview'),
        apply: () => ipcRenderer.invoke('drivers:apply'),
        revert: () => ipcRenderer.invoke('drivers:revert'),
    },
    installer: {
        launch: () => ipcRenderer.invoke('installer:launch'),
    },

    // --- Session (Dev vs Desktop via the boot greeter) ---------------------
    auth: {
        // Sign-in / lock state (hasPin, lockEnabled, user, live).
        status: () => ipcRenderer.invoke('auth:status'),
        // Unlock with a 4-digit PIN or the account password. {pin} | {password}.
        unlock: (payload) => ipcRenderer.invoke('auth:unlock', payload),
        // Set or change the PIN (pass {pin}; if one exists, also {current}).
        setPin: (pin, current) => ipcRenderer.invoke('auth:set-pin', { pin, current }),
        // Remove the PIN (needs the current PIN or the account password).
        clearPin: (payload) => ipcRenderer.invoke('auth:clear-pin', payload),
        // Toggle the startup sign-in screen.
        setLock: (enabled) => ipcRenderer.invoke('auth:set-lock', enabled),
        // One-shot: did the greeter already take the PIN moments ago? Consumes the token.
        recentlyUnlocked: () => ipcRenderer.invoke('auth:recently-unlocked'),
    },
    net: {
        // Connectivity + device summary (full|limited|portal|none|unknown).
        status: () => ipcRenderer.invoke('net:status'),
        // Scan for Wi-Fi networks (sorted: connected first, then by signal).
        wifiList: () => ipcRenderer.invoke('net:wifi-list'),
        // Connect to an SSID; password optional for open networks.
        wifiConnect: (ssid, password) => ipcRenderer.invoke('net:wifi-connect', { ssid, password }),
        // Airplane mode — read + toggle all radios off/on.
        airplaneStatus: () => ipcRenderer.invoke('net:airplane-status'),
        setAirplane: (on) => ipcRenderer.invoke('net:airplane-set', on),
    },
    session: {
        // Mark the next X session as Dev (Outlaw CodeMaker), bypassing the
        // greeter once, and end this X session so the switch can happen.
        switchToDev: () => ipcRenderer.invoke('session:switch-dev'),
        // Is the Dev session runnable here yet? (PyQt6 importable.) Lets the UI
        // offer to download the dev environment before attempting a switch.
        devStatus: () => ipcRenderer.invoke('session:dev-status'),
        // Download + build the Dev environment on demand (opens a terminal).
        setupDev: () => ipcRenderer.invoke('session:setup-dev'),
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
