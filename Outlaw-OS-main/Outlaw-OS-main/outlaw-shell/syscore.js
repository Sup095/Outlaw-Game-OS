// ============================================================================
// Outlaw OS — System Core (SC1)
// ----------------------------------------------------------------------------
// The sci-fi centerpiece + bubble + state machine. SC1 is the *scaffold only*
// — no data, no AI, no diagnostics. Later slices plug into the lifecycle hooks
// and state API exposed here.
//
// Design rules (lifted straight from the roadmap):
//   * Nothing runs unless the user is looking at the System Core screen. We
//     gate every timer/animation behind two signals: showScreen('syscore')
//     and document.visibilityState. Either going false pauses everything.
//   * No canvas, no WebGL, no requestAnimationFrame loops. The visual is pure
//     CSS animations driven by class toggles. JS does state transitions only.
//   * No new IPC. No LM Studio calls. No network. SC1 is offline-by-construction.
//
// State machine (set via setCoreState):
//
//        ┌──────► idle ◄────────────┐
//        │         │                │
//        │         ▼                │
//        │     listening            │
//        │         │                │
//        │         ▼                │
//        └─── speaking ───► working ┘
//
// Transitions are best-effort — any unknown state collapses to `idle` so the
// UI can never get stuck in a glow loop. The transitions are not enforced
// (anything → anything is allowed) because real-world callers (SC3 diagnostics,
// SC6 live AI) move between them in ways we don't want to over-constrain.
// ============================================================================
'use strict';

(function () {
    const VALID_STATES = ['idle', 'listening', 'speaking', 'working'];
    const STATE_CLASSES = VALID_STATES.map((s) => 'is-' + s);
    const ACTIVE_CLASS = 'is-active';   // any non-idle state activates animations
    const DEFAULT_BUBBLE_TTL_MS = 4000;
    const MIN_BUBBLE_TTL_MS = 1500;
    const MAX_BUBBLE_TTL_MS = 30000;
    const STATS_POLL_MS = 2000;         // SC2 — cadence of the live readouts
    const FRESH_FLASH_MS = 600;         // SC2 — duration of the per-tile update highlight

    // --- module state -------------------------------------------------------
    let _initialized = false;     // DOM wiring done once
    let _visible = false;         // user is on the System Core screen
    let _state = 'idle';
    let _bubbleTimer = null;
    let _pageVisibilityHandler = null;
    // SC2 poller state. The stats timer is the ONLY long-lived JS work this
    // module ever does, and it only ticks while the user is on the screen.
    let _statsTimer = null;
    let _lastNetSample = null;
    let _uptimeBaseline = null;
    let _statsInFlight = false;
    // SC3 diagnostics state. The runner lives in main; the renderer just
    // subscribes to progress while visible and unsubscribes on teardown.
    let _diagUnsubscribe = null;
    let _diagButtonsWired = false;
    let _diagRunning = false;
    // SC5 cold dialogue + TTS state. _bootGreeted ensures the boot_complete
    // line fires once per shell process, not on every re-visit. The idle
    // ticker is a chained setTimeout so we never queue more than one pending
    // tick (a runaway setInterval would be the wrong pattern here).
    let _bootGreeted = false;
    let _idleTimer = null;
    let _voiceStatus = null;   // last fetched tts:status payload
    // SC7 VRAM tier — most recently observed status. Stays cached across
    // re-visits so the badge has something to paint immediately while we
    // wait for vram:status to resolve.
    let _vramStatus = null;
    let _vramUnsubscribe = null;
    // SC6 Live AI — flips between cold-dialogue and LM Studio-backed Live mode.
    // sessionId is generated lazily on first Live activation and persists
    // across re-visits within the same shell process (so the conversation
    // survives a casual screen change). Reset button clears the server-side
    // history + makes a fresh id.
    let _liveMode = false;
    let _liveSessionId = null;
    let _liveBusy = false;
    let _liveButtonsWired = false;

    function $$(sel) { return document.querySelector(sel); }

    // --- public API ---------------------------------------------------------

    /**
     * Called by renderer.js whenever the user navigates TO the System Core
     * screen. Idempotent — wiring happens at most once per page load.
     */
    function initSysCore() {
        if (!_initialized) _wireOnce();
        _visible = true;
        _applyAnimationGate();
        // SC2 data loaders. Inventory is a one-shot per visit; stats spin up
        // the 2s poller. Both bail if window.outlaw isn't there yet (preload
        // bridge not established) and try again on the next visit.
        _loadInventory();
        _startStatsPoller();
        _setFooter('core online · live telemetry');
        // SC3 — wire diagnostics buttons (once), subscribe to progress events,
        // and resync from the runner in case a run is already in flight from
        // a previous visit. NEVER aborts an in-flight run on tear-down — that
        // would defeat the point of long-running thorough scans.
        _wireDiagnosticsOnce();
        _subscribeDiagnostics();
        _resyncDiagnosticsState();
        _loadDiagHistory();
        // SC5 — refresh voice engine status (drives the footer text), greet
        // once per process, and start the idle banter ticker. Boot greet is
        // deferred briefly so it lands AFTER the screen has animated in.
        _refreshVoiceStatus();
        if (!_bootGreeted) {
            _bootGreeted = true;
            setTimeout(() => { if (_visible) _coldSay('boot_complete'); }, 700);
        }
        _startIdleLoop();
        // SC7 — fetch the current VRAM tier so the header badge has accurate
        // text immediately + subscribe to live transitions while the screen
        // is open.
        _subscribeVramTier();
        _refreshVramTier();
        // SC6 — wire the Live mode toggle + input handlers once; sync UI to
        // the current _liveMode flag (preserved across re-visits).
        _wireLiveOnce();
        _syncLiveUi();
        // P2 — System Control quick-actions card. Wire once; refresh the perf
        // label to reflect the current setting each visit.
        _wireControlOnce();
        _refreshControlState();
    }

    /**
     * Called by renderer.js whenever the user navigates AWAY from the System
     * Core screen. Stops everything we control so the page contributes zero
     * load to the rest of the shell. Safe to call repeatedly.
     */
    function teardownSysCore() {
        _visible = false;
        // Drop any pending bubble fade — we don't want a stale line popping
        // back in if the user returns to the screen later.
        _clearBubbleTimer();
        _hideBubble();
        _setStateInternal('idle');
        _applyAnimationGate();
        // SC2: stop every timer + drop cached samples. After this returns the
        // System Core contributes zero load to the rest of the shell.
        _stopStatsPoller();
        _lastNetSample = null;
        _uptimeBaseline = null;
        _resetReadouts();
        _setFooter('core dormant · no AI traffic · no diagnostics');
        // SC3 — drop the renderer-side subscription so progress events from a
        // background run aren't queued in this hidden DOM. The run itself
        // keeps going in main; resync picks it up on the next visit.
        _unsubscribeDiagnostics();
        // SC5 — stop the idle ticker and silence any in-flight TTS line so a
        // 4-second sentence doesn't continue playing while the user is in
        // Files or Steam. The voice doesn't follow you out of the room.
        _stopIdleLoop();
        if (window.outlaw && window.outlaw.tts) {
            window.outlaw.tts.cancel().catch(() => {});
        }
        // SC7 — drop the vram-tier-changed subscription. Main keeps polling
        // (other features may want it), but we don't need updates while
        // the screen is hidden.
        _unsubscribeVramTier();
    }

    /**
     * Move the core's visible state. Unknown values collapse to 'idle' so the
     * UI never wedges. State changes only have visible effect while the user
     * is on the screen — calling this off-screen just records intent.
     */
    function setCoreState(next) {
        const s = (next || '').toString().toLowerCase();
        if (!VALID_STATES.includes(s)) {
            console.warn('[syscore] unknown state %s — collapsing to idle', s);
            _setStateInternal('idle');
        } else {
            _setStateInternal(s);
        }
        _applyAnimationGate();
    }

    /**
     * Drop a line of text in the bubble. Auto-fades after `ttl` ms. SC5 will
     * pair this with TTS; SC6 with live AI responses. SC1 just exposes the
     * primitive so we can smoke-test the visual from the dev console.
     */
    function coreSay(text, ttl) {
        const bubble = $$('#core-bubble');
        if (!bubble) return;
        const safe = (text == null ? '' : String(text)).slice(0, 240);
        if (!safe) {
            _hideBubble();
            return;
        }
        bubble.textContent = safe;
        bubble.classList.add('show');
        _clearBubbleTimer();
        const lifetime = Math.max(
            MIN_BUBBLE_TTL_MS,
            Math.min(MAX_BUBBLE_TTL_MS, Number(ttl) || DEFAULT_BUBBLE_TTL_MS),
        );
        // setTimeout is the only ongoing JS work this module does while idle;
        // it's a single one-shot per line, no polling.
        _bubbleTimer = setTimeout(() => {
            _bubbleTimer = null;
            _hideBubble();
        }, lifetime);
    }

    /**
     * Returns a snapshot of internal state. Useful for tests and the upcoming
     * SC7 status badge that reads the core's current mode.
     */
    function getCoreInfo() {
        return {
            initialized: _initialized,
            visible: _visible,
            state: _state,
            documentHidden: typeof document !== 'undefined'
                && document.visibilityState === 'hidden',
        };
    }

    // --- internals ----------------------------------------------------------

    function _wireOnce() {
        _initialized = true;
        // Pause/resume animations when the OS window itself is hidden (lock
        // screen, alt-tab, kiosk OFF). Belt-and-suspenders — most browsers
        // already pause CSS animations behind display:none and minimized windows,
        // but we make it explicit here so the perf budget holds on every
        // compositor we might encounter.
        _pageVisibilityHandler = () => _applyAnimationGate();
        document.addEventListener('visibilitychange', _pageVisibilityHandler);
    }

    function _setStateInternal(s) {
        _state = s;
        const root = $$('#screen-syscore');
        if (!root) return;
        // Strip all state classes, then add the one for the current state.
        // The `.is-active` flag is implied by any non-idle state.
        for (const cls of STATE_CLASSES) root.classList.remove(cls);
        root.classList.add('is-' + s);
    }

    function _applyAnimationGate() {
        const root = $$('#screen-syscore');
        if (!root) return;
        // Animations only run when ALL of these are true:
        //   1. The user is on the System Core screen (_visible).
        //   2. The OS window itself is visible (documentVisible).
        //   3. The state is not 'idle' (some activity is happening).
        const documentVisible = document.visibilityState !== 'hidden';
        const shouldAnimate = _visible && documentVisible && _state !== 'idle';
        root.classList.toggle(ACTIVE_CLASS, shouldAnimate);
    }

    function _hideBubble() {
        const bubble = $$('#core-bubble');
        if (!bubble) return;
        bubble.classList.remove('show');
        // Defer the textContent clear until after the fade so the user doesn't
        // see the text vanish mid-transition.
        setTimeout(() => {
            if (!bubble.classList.contains('show')) bubble.textContent = '';
        }, 260);
    }

    function _clearBubbleTimer() {
        if (_bubbleTimer != null) {
            clearTimeout(_bubbleTimer);
            _bubbleTimer = null;
        }
    }

    // --- SC2: stats poller + inventory loader -------------------------------

    function _startStatsPoller() {
        if (_statsTimer || !window.outlaw) return;
        // Tick immediately so the user sees numbers within ~300ms instead of
        // waiting a full 2s for the first cycle.
        _pollStats();
        _statsTimer = setInterval(_pollStats, STATS_POLL_MS);
    }

    function _stopStatsPoller() {
        if (_statsTimer != null) {
            clearInterval(_statsTimer);
            _statsTimer = null;
        }
        _statsInFlight = false;
    }

    async function _pollStats() {
        const api = window.outlaw && window.outlaw.system;
        if (!api || !_visible) return;
        // Guard against overlapping ticks on a slow box — if a poll runs
        // longer than the interval, just skip the next one rather than
        // queueing more work.
        if (_statsInFlight) return;
        _statsInFlight = true;
        try {
            const [stats, gpu, disk, net] = await Promise.all([
                api.stats ? api.stats().catch(() => null) : null,
                api.gpuDetailed ? api.gpuDetailed().catch(() => null) : null,
                api.disk ? api.disk().catch(() => null) : null,
                api.net ? api.net().catch(() => null) : null,
            ]);
            // User may have navigated away during the await — bail before
            // touching DOM to avoid flashing stale data on the next visit.
            if (!_visible) return;
            if (stats) _renderCpuMem(stats);
            if (gpu) _renderGpu(gpu);
            if (disk) _renderDisk(disk);
            if (net) _renderNet(net);
            _renderUptime();
        } finally {
            _statsInFlight = false;
        }
    }

    async function _loadInventory() {
        const api = window.outlaw && window.outlaw.system;
        if (!api) return;
        try {
            const [inv, info] = await Promise.all([
                api.inventory ? api.inventory().catch(() => null) : null,
                api.info ? api.info().catch(() => null) : null,
            ]);
            if (!_visible) return;
            if (info) {
                // Cache the baseline for the local uptime ticker. Each render
                // adds (Date.now() - t)/1000 — no extra IPC per tick.
                _uptimeBaseline = { sec: info.uptime || 0, t: Date.now() };
                _setText('inv-host', info.hostname);
                _setText('inv-kernel', info.kernel);
                _setText('inv-shell', 'v' + (info.appVersion || '?'));
            }
            if (inv) {
                _setText('inv-pkgs', inv.packages
                    ? inv.packages.toLocaleString() + ' installed'
                    : (inv.available ? '0' : 'n/a'));
                _setText('inv-sesspref', _prettySessionPref(inv.sessionPref));
                _setText('inv-boot', inv.bootSince || (inv.available ? '—' : 'n/a'));
                _setText('inv-snap', inv.snapshotMb
                    ? inv.snapshotMb + ' MB'
                    : (inv.available ? '0 MB' : 'n/a'));
                _setText('inv-aa', _styleStatus('inv-aa', inv.apparmor));
                _setText('inv-ufw', _styleStatus('inv-ufw', inv.ufw));
                _setText('sc-inv-status', inv.available ? 'ok' : 'preview · off-Linux');
            }
        } catch (e) {
            console.warn('[syscore] inventory load failed:', e);
        }
    }

    // --- SC2: renderers (DOM-only, no compute) ------------------------------

    function _renderCpuMem(s) {
        const cpu = Math.round(Number(s.cpu) || 0);
        _setReadout('sc-cpu', cpu + '%', s.time ? 'sample ' + s.time : '');
        const ramPct = Math.round(Number(s.ramPct) || 0);
        const used = s.ramUsed || '—';
        const total = s.ramTotal || '—';
        _setReadout('sc-mem', used + ' / ' + total, ramPct + '% used');
    }

    function _renderGpu(g) {
        if (!g.available) {
            _setReadout('sc-gpu', 'n/a', 'preview · off-Linux');
            return;
        }
        const name = (g.name || 'GPU').slice(0, 28);
        // Show live VRAM whenever we have a total — NVIDIA (nvml) or AMD/Intel
        // discrete cards (drm sysfs). Integrated GPUs share system RAM and report
        // no dedicated VRAM, so we just name the chip.
        if (g.vramTotalMb > 0) {
            const used = Math.round(g.vramUsedMb);
            const total = Math.round(g.vramTotalMb);
            _setReadout('sc-gpu', used + ' / ' + total + ' MB', name + ' · ' + g.vramPct + '%');
        } else {
            _setReadout('sc-gpu', name, 'shared memory · no dedicated VRAM');
        }
    }

    function _renderDisk(d) {
        if (!d.available) {
            _setReadout('sc-disk', 'n/a', 'preview · off-Linux');
            return;
        }
        const used = _fmtMb(d.usedMb);
        const total = _fmtMb(d.totalMb);
        _setReadout('sc-disk', used + ' / ' + total, d.pct + '% used · ' + d.mount);
    }

    function _renderNet(n) {
        if (!n.available) {
            _setReadout('sc-net', 'n/a', 'preview · off-Linux');
            _lastNetSample = null;
            return;
        }
        if (!_lastNetSample) {
            _lastNetSample = n;
            _setReadout('sc-net', '… measuring', 'first sample');
            return;
        }
        const dt = Math.max(0.001, (n.t - _lastNetSample.t) / 1000);
        const dRx = Math.max(0, n.rxBytes - _lastNetSample.rxBytes);
        const dTx = Math.max(0, n.txBytes - _lastNetSample.txBytes);
        _lastNetSample = n;
        const rxRate = _fmtRate(dRx / dt);
        const txRate = _fmtRate(dTx / dt);
        _setReadout('sc-net', '↓ ' + rxRate + '  ↑ ' + txRate, 'all interfaces');
    }

    function _renderUptime() {
        if (!_uptimeBaseline) {
            _setReadout('sc-uptime', 'measuring', '');
            return;
        }
        const seconds = _uptimeBaseline.sec + (Date.now() - _uptimeBaseline.t) / 1000;
        _setReadout('sc-uptime', _fmtUptime(seconds), 'since boot');
    }

    function _resetReadouts() {
        for (const id of ['sc-cpu', 'sc-mem', 'sc-gpu', 'sc-disk', 'sc-net', 'sc-uptime']) {
            const el = document.getElementById(id);
            if (!el) continue;
            const v = el.querySelector('.value');
            if (v) v.textContent = '—';
            el.classList.add('empty');
            el.classList.remove('fresh');
        }
    }

    // --- SC2: small DOM + formatting helpers --------------------------------

    function _setReadout(id, value, sub) {
        const el = document.getElementById(id);
        if (!el) return;
        const v = el.querySelector('.value');
        const s = el.querySelector('.sub');
        const before = v ? v.textContent : '';
        if (v) v.textContent = value;
        if (s && sub != null) s.textContent = sub;
        el.classList.remove('empty');
        // Flash highlight only on value change — avoid distracting strobing
        // when a number happens to stay flat for several ticks.
        if (before !== value) {
            el.classList.remove('fresh');
            void el.offsetWidth;  // restart the animation
            el.classList.add('fresh');
            setTimeout(() => el && el.classList.remove('fresh'), FRESH_FLASH_MS + 100);
        }
    }

    function _setText(id, text) {
        const el = document.getElementById(id);
        if (el && text != null) el.textContent = String(text);
    }

    function _setFooter(text) {
        const el = document.getElementById('core-footer-msg');
        if (el) el.textContent = text;
    }

    function _prettySessionPref(p) {
        if (!p || p === 'ask') return 'ask each boot';
        return 'always ' + p;
    }

    function _styleStatus(elId, raw) {
        const el = document.getElementById(elId);
        const text = String(raw || 'unknown').toLowerCase();
        if (el) {
            el.classList.remove('status-ok', 'status-warn', 'status-bad');
            if (text.includes('active') || text === 'enabled' || text === 'running') {
                el.classList.add('status-ok');
            } else if (text === 'inactive' || text === 'disabled' || text === 'stopped') {
                el.classList.add('status-bad');
            } else {
                el.classList.add('status-warn');
            }
        }
        return text;
    }

    function _fmtMb(mb) {
        if (mb == null) return '—';
        if (mb >= 1024 * 1024) return (mb / 1024 / 1024).toFixed(2) + ' TB';
        if (mb >= 1024) return (mb / 1024).toFixed(1) + ' GB';
        return Math.round(mb) + ' MB';
    }

    function _fmtRate(bps) {
        if (!isFinite(bps) || bps < 0) bps = 0;
        if (bps < 1024) return Math.round(bps) + ' B/s';
        if (bps < 1024 * 1024) return (bps / 1024).toFixed(1) + ' KB/s';
        if (bps < 1024 * 1024 * 1024) return (bps / 1024 / 1024).toFixed(1) + ' MB/s';
        return (bps / 1024 / 1024 / 1024).toFixed(2) + ' GB/s';
    }

    function _fmtUptime(seconds) {
        const s = Math.max(0, Math.floor(seconds));
        const days = Math.floor(s / 86400);
        const hours = Math.floor((s % 86400) / 3600);
        const mins = Math.floor((s % 3600) / 60);
        const secs = s % 60;
        if (days > 0) return days + 'd ' + hours + 'h ' + mins + 'm';
        if (hours > 0) return hours + 'h ' + mins + 'm';
        if (mins > 0) return mins + 'm ' + secs + 's';
        return secs + 's';
    }

    // --- SC3: diagnostics ---------------------------------------------------

    function _wireDiagnosticsOnce() {
        if (_diagButtonsWired) return;
        _diagButtonsWired = true;
        const root = $$('#sc-diagnostics');
        if (!root) return;
        // Profile buttons — one click delegate, three triggers.
        root.addEventListener('click', (e) => {
            const startBtn = e.target.closest('[data-diag-profile]');
            if (startBtn) {
                const profile = startBtn.dataset.diagProfile;
                _runDiagnostic(profile).catch((err) => {
                    coreSay('Diagnostic refused to start: ' + err.message);
                });
                return;
            }
            if (e.target.closest('#diag-cancel')) {
                _cancelDiagnostic();
            }
            // SC4 — "Run now" on a schedule row triggers the systemd instance
            // directly (works whether the timer is enabled or not).
            const runNowBtn = e.target.closest('.diag-sched-runnow');
            if (runNowBtn) {
                const row = runNowBtn.closest('[data-sched-id]');
                if (row) _runScheduledNow(row.dataset.schedId);
                return;
            }
        });
        // Schedule toggles — change event so we don't fire on focus etc.
        root.addEventListener('change', (e) => {
            const toggle = e.target.closest('[data-sched-toggle]');
            if (toggle) {
                const row = toggle.closest('[data-sched-id]');
                if (row) _toggleScheduled(row.dataset.schedId, toggle.checked);
            }
        });
        // History list lazy-loads when the user expands the details element.
        const hist = root.querySelector('.diag-history');
        if (hist) {
            hist.addEventListener('toggle', () => {
                if (hist.open) _loadDiagHistory();
            });
        }
        // Schedule rows refresh whenever the user expands the schedule details.
        const sched = root.querySelector('#diag-schedule');
        if (sched) {
            sched.addEventListener('toggle', () => {
                if (sched.open) _refreshSchedules();
            });
        }
    }

    function _subscribeDiagnostics() {
        if (_diagUnsubscribe) return;
        const api = window.outlaw;
        if (!api || typeof api.on !== 'function') return;
        _diagUnsubscribe = api.on('diagnostics-progress', _onDiagProgress);
    }

    function _unsubscribeDiagnostics() {
        if (typeof _diagUnsubscribe === 'function') {
            try { _diagUnsubscribe(); } catch { /* ignored */ }
        }
        _diagUnsubscribe = null;
    }

    async function _resyncDiagnosticsState() {
        const api = window.outlaw && window.outlaw.diagnostics;
        if (!api) return;
        try {
            const st = await api.status();
            if (!_visible) return;
            if (!st || !st.running) {
                _setDiagUiRunning(false);
                _setDiagStatus('idle');
                return;
            }
            // Re-hydrate the in-flight run's progress UI.
            _setDiagUiRunning(true);
            _renderDiagResults(st.results || []);
            _setDiagBar(st.idx, st.total);
            _setDiagProgressMsg(
                `Test ${Math.max(1, st.idx + 1)} of ${st.total} (${st.profile})`,
            );
            _setDiagStatus(`running · ${st.profile}`);
        } catch (e) {
            console.warn('[syscore] diagnostics status resync failed:', e);
        }
    }

    async function _runDiagnostic(profile) {
        const api = window.outlaw && window.outlaw.diagnostics;
        if (!api) {
            coreSay('Diagnostics bridge is offline.');
            return;
        }
        if (_diagRunning) {
            coreSay('A sweep is already running.');
            return;
        }
        const r = await api.run(profile);
        if (!r || r.ok === false) {
            coreSay('Diagnostic refused: ' + ((r && r.error) || 'unknown'));
        }
        // The 'start' progress event will flip the UI; we don't update here.
    }

    async function _cancelDiagnostic() {
        const api = window.outlaw && window.outlaw.diagnostics;
        if (!api) return;
        await api.cancel().catch(() => {});
        coreSay('Cancellation requested.');
    }

    function _onDiagProgress(ev) {
        if (!ev || !_visible) return;
        switch (ev.phase) {
            case 'start': {
                _diagRunning = true;
                _setDiagUiRunning(true);
                _clearDiagResults();
                _setDiagBar(0, ev.total || 1);
                _setDiagStatus(`running · ${ev.profile}`);
                _setDiagProgressMsg(`Spinning up ${ev.total} test(s)…`);
                setCoreState('working');
                _coldSay('diagnostic_started_' + ev.profile, null, 5000);
                break;
            }
            case 'test-start': {
                _setDiagBar(ev.idx, ev.total);
                _setDiagProgressMsg(
                    `Test ${ev.idx + 1} of ${ev.total} · ${ev.label || ev.name}`,
                );
                break;
            }
            case 'test-done': {
                _appendDiagResult(ev.result);
                _setDiagBar(ev.idx + 1, ev.total);
                break;
            }
            case 'cancelled': {
                _diagRunning = false;
                _setDiagUiRunning(false);
                _setDiagStatus('cancelled');
                _setDiagProgressMsg('Cancelled.');
                setCoreState('idle');
                _coldSay('diagnostic_cancelled');
                break;
            }
            case 'error': {
                _diagRunning = false;
                _setDiagUiRunning(false);
                _setDiagStatus('error');
                _setDiagProgressMsg('Error: ' + (ev.error || 'unknown'));
                setCoreState('idle');
                _coldSay('diagnostic_error', null, 6000);
                break;
            }
            case 'done': {
                _diagRunning = false;
                _setDiagUiRunning(false);
                const s = (ev.report && ev.report.summary) || {};
                const pass = s.pass || 0, warn = s.warn || 0,
                      fail = s.fail || 0, skip = s.skip || 0;
                _setDiagStatus(`done · ${pass}✓ ${warn}⚠ ${fail}✕ ${skip}–`);
                _setDiagProgressMsg(
                    `Saved → ${ev.report && ev.report.savedTo || '~/.outlaw-diagnostics/'}`,
                );
                setCoreState('idle');
                if (fail > 0) {
                    _coldSay('diagnostic_complete_failures', { n: fail }, 6000);
                } else if (warn > 0) {
                    _coldSay('diagnostic_complete_warnings', { n: warn }, 6000);
                } else if (skip > 0 && pass === 0) {
                    // Off-OS preview: every test skipped. Use a plain coreSay
                    // since this case isn't in the cold catalogue.
                    coreSay('Sweep complete. All tests skipped — running off-OS?', 6000);
                } else {
                    _coldSay('diagnostic_complete_clean', null, 6000);
                }
                _loadDiagHistory();  // refresh history dropdown
                break;
            }
        }
    }

    // ----- diagnostics DOM helpers ------------------------------------------

    function _setDiagUiRunning(running) {
        const start = document.querySelectorAll('#sc-diagnostics [data-diag-profile]');
        const cancelBtn = $$('#diag-cancel');
        const progress = $$('#diag-progress');
        for (const b of start) b.disabled = running;
        if (cancelBtn) cancelBtn.hidden = !running;
        if (progress) progress.hidden = !running;
    }

    function _setDiagStatus(text) {
        const el = $$('#sc-diag-status');
        if (el) el.textContent = text;
    }

    function _setDiagProgressMsg(text) {
        const el = $$('#diag-progress-msg');
        if (el) el.textContent = text;
    }

    function _setDiagBar(idx, total) {
        const el = $$('#diag-bar');
        if (!el) return;
        const pct = total > 0 ? Math.min(100, Math.max(0, (idx / total) * 100)) : 0;
        // transform: scaleX — compositor-only, no layout.
        el.style.transform = `scaleX(${(pct / 100).toFixed(3)})`;
    }

    function _clearDiagResults() {
        const list = $$('#diag-results');
        if (list) list.innerHTML = '';
    }

    function _renderDiagResults(results) {
        const list = $$('#diag-results');
        if (!list) return;
        list.innerHTML = '';
        for (const r of results) _appendDiagResult(r);
    }

    function _appendDiagResult(r) {
        const list = $$('#diag-results');
        if (!list || !r) return;
        const status = (r.status || 'skip').toLowerCase();
        const row = document.createElement('div');
        row.className = `diag-row diag-${status}`;
        const safeLabel = _escape(r.label || r.name || '(unnamed)');
        const safeDetail = _escape(r.detail || '');
        const safeHint = r.hint ? `<div class="diag-hint">↳ ${_escape(r.hint)}</div>` : '';
        const dur = r.durationMs ? `<span class="diag-dur">${r.durationMs}ms</span>` : '';
        row.innerHTML =
            `<div class="diag-row-head">` +
                `<span class="diag-pip"></span>` +
                `<span class="diag-name">${safeLabel}</span>` +
                `<span class="diag-status">${status}</span>` +
                dur +
            `</div>` +
            `<div class="diag-detail">${safeDetail}</div>` +
            safeHint;
        list.appendChild(row);
    }

    async function _loadDiagHistory() {
        const api = window.outlaw && window.outlaw.diagnostics;
        const target = $$('#diag-history-list');
        if (!target || !api) return;
        try {
            const files = await api.listReports();
            if (!files || files.length === 0) {
                target.innerHTML = '<span class="dim">(no past reports)</span>';
                return;
            }
            target.innerHTML = files.slice(0, 10)
                .map((f) => `<div>${_escape(f)}</div>`)
                .join('');
        } catch (e) {
            target.innerHTML = '<span class="dim">history unavailable</span>';
        }
    }

    function _escape(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }

    // --- SC4: scheduled diagnostic checks -----------------------------------

    async function _refreshSchedules() {
        const api = window.outlaw && window.outlaw.scheduled;
        if (!api) return;
        let status;
        try { status = await api.status(); } catch { return; }
        if (!_visible || !status) return;
        for (const id of Object.keys(status)) {
            const st = status[id];
            const row = document.querySelector(`#diag-schedule [data-sched-id="${id}"]`);
            if (!row || !st) continue;
            const toggle = row.querySelector('[data-sched-toggle]');
            const next = row.querySelector('[data-sched-next]');
            const last = row.querySelector('[data-sched-last]');
            const runNow = row.querySelector('.diag-sched-runnow');
            if (toggle) toggle.checked = !!st.enabled;
            if (next) next.textContent = 'next: ' + _formatSystemdTime(st.nextRun, 'never');
            if (last) {
                let lastTxt = 'last: ' + _formatSystemdTime(st.lastRun, 'never');
                if (st.lastResult && st.lastResult !== 'success' && st.lastResult !== '') {
                    lastTxt += ' (' + st.lastResult + ')';
                }
                last.textContent = lastTxt;
            }
            // Off-Linux: gray out the controls so it's clear they don't apply.
            const dim = !st.available;
            if (toggle) toggle.disabled = dim;
            if (runNow) runNow.disabled = dim;
        }
    }

    async function _toggleScheduled(id, enable) {
        const api = window.outlaw && window.outlaw.scheduled;
        if (!api) return;
        const r = enable ? await api.enable(id) : await api.disable(id);
        if (!r || r.ok === false) {
            coreSay('Could not flip schedule: ' + ((r && r.error) || 'unknown'));
            // Refresh anyway so the toggle reflects actual state.
            _refreshSchedules();
            return;
        }
        // Notice line on success (uses the dialogue notice bucket).
        _coldSay('notice');
        _refreshSchedules();
    }

    async function _runScheduledNow(id) {
        const api = window.outlaw && window.outlaw.scheduled;
        if (!api) return;
        const r = await api.runNow(id);
        if (!r || r.ok === false) {
            coreSay('Could not start schedule run: ' + ((r && r.error) || 'unknown'));
            return;
        }
        // The scheduled run executes outside the shell — we don't get
        // streaming events. Show a brief confirmation, then refresh status
        // after a beat so "last run" catches up.
        coreSay('Started ' + (r.started || id) + ' sweep in the background.', 5000);
        setTimeout(() => _refreshSchedules(), 4000);
        setTimeout(() => _loadDiagHistory(), 6000);
    }

    function _formatSystemdTime(value, emptyFallback) {
        // systemd reports times as microseconds-since-epoch when set, or
        // "0" / "n/a" when unset. Anything that looks like an ISO/human
        // string we pass through; anything that looks numeric we format.
        if (!value || value === '0' || value === 'n/a' || value === 'infinity') {
            return emptyFallback || 'never';
        }
        // Pure-numeric μs since epoch → Date.
        if (/^\d+$/.test(value)) {
            const ms = Number(value) / 1000;
            if (!isFinite(ms) || ms <= 0) return emptyFallback || 'never';
            try {
                const d = new Date(ms);
                // Compact YYYY-MM-DD HH:MM format — fits one line in the row.
                const iso = d.toISOString().replace('T', ' ').slice(0, 16);
                return iso;
            } catch {
                return String(value).slice(0, 24);
            }
        }
        // Pass-through for human strings, capped so layout doesn't blow out.
        return String(value).slice(0, 24);
    }

    // --- SC5: cold dialogue + TTS -------------------------------------------

    // Tiny hardcoded fallback set — used only while dialogue.json is still
    // loading on the very first frame, or if the JSON failed to parse.
    const _COLD_FALLBACKS = {
        boot_complete: 'Core online. Standing by.',
        idle_loop: 'Subsystems quiet.',
        diagnostic_started_quick: 'Running quick sweep, operator.',
        diagnostic_started_standard: 'Standard diagnostic sweep underway.',
        diagnostic_started_thorough: 'Engaging thorough scan. This will take time.',
        diagnostic_complete_clean: 'Sweep complete. All systems nominal.',
        diagnostic_complete_warnings: 'Sweep complete. {n} warning(s) flagged.',
        diagnostic_complete_failures: 'Sweep complete. {n} failure(s) require attention.',
        diagnostic_cancelled: 'Sweep aborted.',
        diagnostic_error: 'Sweep encountered an error.',
        notice: 'Noted.',
    };

    /**
     * Pick a line for `event` (with {n}-style params), drop it in the bubble,
     * and route it through TTS. TTS is a fire-and-forget — main returns ok
     * with did:'muted' when the user has the voice toggle off, so we don't
     * have to check the setting from here.
     *
     * SC7: when the VRAM tier is `minimal`, we skip the TTS call entirely
     * — even if the user has the voice toggle on. The text bubble still
     * fires so the user gets the information; we just don't fork piper /
     * espeak under VRAM/CPU pressure.
     */
    function _coldSay(event, params, ttl) {
        const dlg = window.outlawDialogue;
        let line = (dlg && dlg.pickSync ? dlg.pickSync(event, params) : null);
        if (line == null) {
            const fb = _COLD_FALLBACKS[event];
            if (fb) {
                // Manual {n} interpolation against the fallback string.
                line = String(fb).replace(/\{(\w+)\}/g, (m, k) =>
                    params && params[k] != null ? params[k] : m);
            }
        }
        if (!line) return null;
        coreSay(line, ttl);
        if (window.outlaw && window.outlaw.tts && !_isEmergencyTier()) {
            window.outlaw.tts.speak(line).catch(() => { /* TTS failures stay silent */ });
        }
        return line;
    }

    function _startIdleLoop() {
        if (_idleTimer) return;
        const tick = () => {
            _idleTimer = null;
            if (!_visible) return;
            // Only speak when truly idle — don't talk over a running diagnostic
            // or an active "speaking" state.
            if (_state === 'idle' && !_diagRunning) {
                _coldSay('idle_loop');
            }
            _idleTimer = setTimeout(tick, _randIdleInterval());
        };
        // First tick is a bit later than the steady-state interval so the boot
        // greeting has room to land before the idle banter starts.
        _idleTimer = setTimeout(tick, _randIdleInterval() + 30_000);
    }

    function _stopIdleLoop() {
        if (_idleTimer != null) {
            clearTimeout(_idleTimer);
            _idleTimer = null;
        }
    }

    function _randIdleInterval() {
        // 90–180 seconds. Chosen so the banter is presence, not pestering.
        return 90_000 + Math.floor(Math.random() * 90_000);
    }

    async function _refreshVoiceStatus() {
        const api = window.outlaw && window.outlaw.tts;
        if (!api) {
            _setFooter('core online · live telemetry · voice: bridge offline');
            return;
        }
        try {
            const st = await api.status();
            if (!_visible) return;
            _voiceStatus = st;
            const desc = _voiceFooterText(st);
            _setFooter('core online · live telemetry · ' + desc);
        } catch {
            // Don't blow away the footer on a probe failure.
        }
    }

    function _voiceFooterText(st) {
        if (!st) return 'voice: unknown';
        if (!st.available) {
            return st.note ? 'voice: ' + (st.note.split('.')[0]).toLowerCase()
                           : 'voice: not installed';
        }
        if (!st.enabled) return 'voice: off (toggle in Settings)';
        return 'voice: ' + st.engine;
    }

    /**
     * Called by renderer.js after a settings change so the footer + cache
     * reflect the user's new preference without waiting for a screen revisit.
     */
    function refreshVoiceStatus() {
        return _refreshVoiceStatus();
    }

    // --- SC7: VRAM tier badge + reactive gating -----------------------------

    function _subscribeVramTier() {
        if (_vramUnsubscribe) return;
        const api = window.outlaw;
        if (!api || typeof api.on !== 'function') return;
        _vramUnsubscribe = api.on('vram-tier-changed', _onVramTierChanged);
    }

    function _unsubscribeVramTier() {
        if (typeof _vramUnsubscribe === 'function') {
            try { _vramUnsubscribe(); } catch { /* ignored */ }
        }
        _vramUnsubscribe = null;
    }

    async function _refreshVramTier() {
        const api = window.outlaw && window.outlaw.vram;
        if (!api) return;
        try {
            const st = await api.status();
            _onVramTierChanged(st);
        } catch { /* probe failed — leave the badge alone */ }
    }

    function _onVramTierChanged(status) {
        if (!status) return;
        _vramStatus = status;
        _paintTierBadge(status);
        // Apply / remove the suppression class so animations honor the
        // emergency tier even when the user is on the screen with a
        // non-idle state. Done by toggling a screen-level class so all of
        // SC1's CSS rules see it.
        const root = $$('#screen-syscore');
        if (root) {
            root.classList.toggle('core-suppressed', _isEmergencyTier());
        }
        // SC6 — if we just entered emergency tier while Live mode is on,
        // force back to Cold and warn the user. Re-toggling will reject
        // until VRAM frees up.
        if (_isEmergencyTier() && _liveMode) {
            _setLiveMode(false);
            coreSay('Core suppressed — VRAM critical. Live mode disabled.', 5000);
        }
        // Refresh button availability either way (label/tooltip may change).
        _syncLiveUi();
    }

    function _paintTierBadge(status) {
        const el = $$('#sc-tier-badge');
        if (!el) return;
        const tier = (status && status.tier) || 'free';
        const label = (status && status.label) || 'VRAM: probing…';
        el.className = 'syscore-tier sc-tier-' + tier;
        // Show a compact label that includes free-mb if we have it.
        if (status && status.available && status.totalMb > 0) {
            el.textContent = label + ' · ' + status.freeMb + ' MB free';
        } else {
            el.textContent = label;
        }
        el.title = 'VRAM saver mode: ' + (status && status.mode || 'auto') +
                   '. Change in Settings → Aggressive VRAM saver.';
    }

    function _isEmergencyTier() {
        return _vramStatus && _vramStatus.tier === 'minimal';
    }

    /**
     * Public entry point used by Settings → Aggressive VRAM saver after the
     * user picks a new mode. Forces a probe (vram:status {force:true} would
     * also work but we want our cached _vramStatus to refresh too).
     */
    function refreshVramTier() {
        return _refreshVramTier();
    }

    // --- SC6: Live AI mode --------------------------------------------------

    function _wireLiveOnce() {
        if (_liveButtonsWired) return;
        _liveButtonsWired = true;
        const toggleBtn = $$('#sc-mode-toggle');
        const sendBtn = $$('#core-input-send');
        const resetBtn = $$('#core-input-reset');
        const inputEl = $$('#core-input-text');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                if (_liveBusy) return;
                if (_isEmergencyTier()) {
                    coreSay('Core suppressed — VRAM critical. Free VRAM first.', 5000);
                    return;
                }
                _setLiveMode(!_liveMode);
            });
        }
        if (sendBtn) sendBtn.addEventListener('click', _sendLive);
        if (resetBtn) resetBtn.addEventListener('click', _resetLiveSession);
        if (inputEl) {
            inputEl.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    _sendLive();
                }
            });
        }
    }

    function _setLiveMode(on) {
        const wantsLive = !!on && !_isEmergencyTier();
        if (wantsLive === _liveMode) return;
        _liveMode = wantsLive;
        // Generate a session id the first time we go live so the model can
        // track conversation. We don't reset it on cold→live→cold flips —
        // user expects to pick up where they left off. Reset button is
        // explicit.
        if (_liveMode && !_liveSessionId) {
            _liveSessionId = 'sc-' + Date.now().toString(36) + '-' +
                Math.random().toString(36).slice(2, 8);
        }
        if (_liveMode) {
            // Stop the cold-mode banter from interrupting the conversation.
            _stopIdleLoop();
            // Brief greeting so the user sees the flip took effect.
            coreSay('Live channel open. Speak.', 3500);
            setCoreState('listening');
        } else {
            // Returning to cold — restart the idle ticker for the banter.
            setCoreState('idle');
            _startIdleLoop();
        }
        _syncLiveUi();
        // Tell the cold-mode footer to reflect the new mode.
        _setFooter(_liveMode
            ? 'live channel · LM Studio · ' + (_voiceStatus
                ? _voiceFooterText(_voiceStatus) : 'voice: probing…')
            : 'core online · live telemetry · ' + (_voiceStatus
                ? _voiceFooterText(_voiceStatus) : 'voice: probing…'));
    }

    function _syncLiveUi() {
        const toggleBtn = $$('#sc-mode-toggle');
        const modeChip = $$('#syscore-mode');
        const inputRow = $$('#core-input');
        const inputEl = $$('#core-input-text');
        const sendBtn = $$('#core-input-send');
        const emergency = _isEmergencyTier();
        if (toggleBtn) {
            toggleBtn.classList.toggle('live', _liveMode);
            toggleBtn.textContent = _liveMode ? 'Go Cold' : 'Go Live';
            toggleBtn.disabled = emergency || _liveBusy;
            toggleBtn.title = emergency
                ? 'Live mode disabled — VRAM is in the Minimal tier. Free VRAM first.'
                : _liveMode
                    ? 'Switch back to Cold mode (canned dialogue, zero AI traffic).'
                    : 'Switch to Live mode (LM Studio answers + tool calls).';
        }
        if (modeChip) {
            modeChip.textContent = _liveMode ? 'LIVE · LM Studio' : 'COLD · standalone';
        }
        if (inputRow) inputRow.hidden = !_liveMode;
        if (inputEl) {
            inputEl.disabled = !_liveMode || _liveBusy;
            if (_liveMode && !_liveBusy) {
                // Focus the input the first time it appears in this visit
                // so the user can type immediately. The check avoids
                // stealing focus on every busy toggle.
                if (document.activeElement !== inputEl) inputEl.focus();
            }
        }
        if (sendBtn) sendBtn.disabled = !_liveMode || _liveBusy;
    }

    async function _sendLive() {
        if (!_liveMode || _liveBusy) return;
        const inputEl = $$('#core-input-text');
        if (!inputEl) return;
        const text = (inputEl.value || '').trim();
        if (!text) return;
        const api = window.outlaw && window.outlaw.coreai;
        if (!api) {
            coreSay('Live channel bridge is offline.', 3000);
            return;
        }
        _liveBusy = true;
        inputEl.value = '';
        _syncLiveUi();
        setCoreState('working');
        coreSay('thinking…', 30000);

        let r;
        try {
            r = await api.ask({ sessionId: _liveSessionId, text });
        } catch (e) {
            r = { ok: false, error: e.message };
        }

        _liveBusy = false;
        if (!r || r.ok === false) {
            const msg = (r && r.error) || 'unknown error';
            coreSay(msg, 6000);
            setCoreState('idle');
            _syncLiveUi();
            return;
        }
        const spoken = (r.intent && r.intent.text) || '(silence)';
        coreSay(spoken, 8000);
        // TTS — same emergency-tier gate as cold _coldSay.
        if (window.outlaw && window.outlaw.tts && !_isEmergencyTier()) {
            window.outlaw.tts.speak(spoken).catch(() => {});
        }
        // Brief 'speaking' beat so the rings react, then settle.
        setCoreState('speaking');
        setTimeout(() => { if (_liveMode) setCoreState('listening'); }, 1800);
        _syncLiveUi();
        // Optional: log what tool actually fired in the dev console for
        // debugging. Not shown to the user — the spoken reply is the UX.
        if (r.did && r.did !== 'answer') {
            console.log('[coreai] tool fired:', r.did, '·', r.detail || '');
        }
    }

    async function _resetLiveSession() {
        if (_liveBusy) return;
        const api = window.outlaw && window.outlaw.coreai;
        if (!api) return;
        if (_liveSessionId) {
            try { await api.reset(_liveSessionId); } catch { /* ignored */ }
        }
        _liveSessionId = 'sc-' + Date.now().toString(36) + '-' +
            Math.random().toString(36).slice(2, 8);
        coreSay('Channel cleared. Fresh start.', 3000);
        const inputEl = $$('#core-input-text');
        if (inputEl) inputEl.focus();
    }

    // --- P2: System Control quick actions -----------------------------------
    // Direct one-click actions that route ONLY through existing audited IPC
    // (updates / gaming-perf / diagnostics / settings nav). No new privileged
    // path, no AI dependency — works whether or not LM Studio is running.
    let _controlWired = false;

    function _setControlStatus(text) {
        const el = $$('#sc-control-status');
        if (el) el.textContent = text || '';
    }

    async function _refreshControlState() {
        try {
            const s = (window.outlaw && window.outlaw.settings)
                ? await window.outlaw.settings.get() : {};
            const on = !!(s && s.performanceMode);
            const lbl = $$('#sc-ctl-perf-label');
            const sub = $$('#sc-ctl-perf-sub');
            if (lbl) lbl.textContent = on ? 'Performance Mode: ON' : 'Performance Mode: OFF';
            if (sub) sub.textContent = on
                ? 'gaming CPU governor active — click to turn off'
                : 'toggle the gaming CPU governor';
        } catch { /* preview / no settings bridge */ }
        // C14 — let the hub INFORM, not just navigate: surface how many issues are
        // in the error log on the Report button (+ flag it if there are any).
        try {
            const eapi = window.outlaw && window.outlaw.errorlog;
            if (eapi && eapi.read) {
                const raw = await eapi.read();
                const text = typeof raw === 'string' ? raw : ((raw && (raw.text || raw.log)) || '');
                const count = text.split('\n').filter((l) => l.trim()).length;
                const rsub = $$('#sc-ctl-report-sub');
                const rbtn = document.querySelector('[data-sc-control="report"]');
                if (rsub) rsub.textContent = count > 0
                    ? (count + ' issue' + (count === 1 ? '' : 's') + ' logged · collect & send')
                    : 'collect & send the error log';
                if (rbtn) rbtn.classList.toggle('has-issues', count > 0);
            }
        } catch { /* no error-log bridge */ }
    }

    function _wireControlOnce() {
        if (_controlWired) return;
        const root = $$('#sc-control');
        if (!root) return;
        _controlWired = true;
        root.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-sc-control]');
            if (!btn) return;
            _runControl(btn.getAttribute('data-sc-control'))
                .catch((err) => _setControlStatus('error: ' + ((err && err.message) || err)));
        });
    }

    async function _runControl(action) {
        const o = window.outlaw || {};
        switch (action) {
            case 'update-os': {
                if (!o.updates) { _setControlStatus('updates unavailable in preview.'); return; }
                _setControlStatus('checking for an Outlaw OS update…');
                const info = await o.updates.checkShell();
                if (!info || !info.ok) { _setControlStatus((info && info.error) || 'check failed.'); return; }
                if (!info.available) { _setControlStatus(`up to date (v${info.currentVersion}).`); return; }
                _setControlStatus(`installing v${info.remoteVersion}…`);
                const r = await o.updates.installShell(info);
                _setControlStatus((r && r.ok)
                    ? `updated to v${info.remoteVersion} — restart to apply.`
                    : ((r && r.error) || 'install failed.'));
                break;
            }
            case 'update-packages': {
                if (!o.updates) { _setControlStatus('updates unavailable in preview.'); return; }
                _setControlStatus('updating system packages (this can take a while)…');
                const r = await o.updates.apply();
                _setControlStatus((r && r.ok) ? 'packages up to date.' : ((r && r.error) || 'update failed.'));
                break;
            }
            case 'perf': {
                if (!o.gaming) { _setControlStatus('not available in preview.'); return; }
                const s = o.settings ? await o.settings.get() : {};
                const next = !(s && s.performanceMode);
                await o.gaming.setPerformance(next);
                if (o.settings) await o.settings.set({ performanceMode: next });
                await _refreshControlState();
                _setControlStatus('performance mode ' + (next ? 'ON' : 'OFF'));
                break;
            }
            case 'diagnostics': {
                _setControlStatus('');
                await _runDiagnostic('quick');
                break;
            }
            case 'settings': {
                const nav = document.querySelector('[data-screen="settings"]');
                if (nav) nav.click();
                break;
            }
            // C14 — hub actions: surface the assistant, the error log + screenshot.
            case 'ask-ai': {
                const nav = document.querySelector('[data-screen="ai"]');
                if (nav) nav.click();
                break;
            }
            case 'report': {
                const nav = document.querySelector('[data-screen="settings"]');
                if (nav) nav.click();
                // settings resets scroll to top; jump to the report card after render.
                setTimeout(() => {
                    const c = document.querySelector('#report-card');
                    if (c) c.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }, 90);
                break;
            }
            case 'screenshot': {
                if (window.takeScreenshot) window.takeScreenshot('full');
                break;
            }
        }
    }

    // --- export -------------------------------------------------------------
    // Hung off `window.outlawCore` so renderer.js (and future slices) can find
    // them without ES module plumbing. Also useful for console smoke tests:
    //   outlawCore.setCoreState('speaking'); outlawCore.coreSay('hello operator');
    window.outlawCore = {
        init: initSysCore,
        teardown: teardownSysCore,
        setCoreState,
        coreSay,
        getInfo: getCoreInfo,
        // SC5: renderer (Settings → Core voice toggle) calls this after a
        // settings flip so the footer + voice cache catch up immediately.
        refreshVoiceStatus,
        // SC7: renderer (Settings → Aggressive VRAM saver) calls this after
        // the mode dropdown changes so the badge + suppression class refresh
        // without waiting for the next 10s background poll.
        refreshVramTier,
    };
})();
