// ============================================================================
// Outlaw OS — VRAM tier classifier (SC7)
// ----------------------------------------------------------------------------
// The shell's split-tier VRAM saver. Two layers, one classifier:
//
//   1. ALWAYS-ON FREE SAVINGS (no perf cost, on by default in every tier):
//      - Animations paused unless the System Core screen is visible AND
//        in a non-idle state (SC1 already does this).
//      - Pollers only tick while their screen is visible (SC2, SC3 already
//        do this).
//      - System tray icon / notification handles released on long hides
//        (future polish).
//      - This module never touches those — they're inherent properties of
//        how SC1/SC2/SC3 were built.
//
//   2. CONDITIONAL EMERGENCY MODE (only when triggered):
//      Activated when free VRAM falls under the configured threshold OR the
//      user pins `lean` / `minimal` in Settings. Effects:
//      - System Core voice (TTS) skips even when toggle is on.
//      - System Core animations force-pause regardless of state.
//      - SC6's future Live AI mode auto-disables until VRAM frees up.
//
// Mode values (persisted in shell settings under `vramSaverMode`):
//   - `auto`     — read NVML each poll; tier follows VRAM (default).
//   - `off`      — always FREE tier; nothing trims.
//   - `lean`     — always LEAN tier (force shrink even when VRAM is plenty).
//   - `minimal`  — always MINIMAL tier (most aggressive).
//
// Tier values (what the rest of the shell consumes):
//   - `free`     — green; everything runs.
//   - `lean`     — amber; pollers and animations still run but the renderer
//                  knows to back off where it can (badges turn amber).
//   - `minimal`  — red; renderer gates voice + animations + Live AI.
//
// Non-NVIDIA machines: NVML fails, `auto` collapses to `free` cleanly.
// Off-Linux: `nvidia-smi` invocation no-ops; same result.
// ============================================================================
'use strict';

const { execFile } = require('child_process');
const { EventEmitter } = require('events');

// Tier thresholds (megabytes). Match the CodeMaker VRAMSaver thresholds so
// both halves of Outlaw OS classify identically on the same hardware.
const MINIMAL_BELOW_MB = 600;
const LEAN_BELOW_MB = 1500;

// Poll cache lifetime — `getTier()` and `getStatus()` reuse a recent read
// instead of slamming nvidia-smi every time. 5s is fast enough that a tier
// transition feels live but slow enough that 200 status calls don't fork
// 200 subprocesses.
const PROBE_TTL_MS = 5_000;

// Background poll cadence when the EventEmitter has subscribers. We don't
// poll when no one is listening — the cache TTL handles intermittent demand.
const SUBSCRIBED_POLL_MS = 10_000;

const VALID_MODES = new Set(['auto', 'off', 'lean', 'minimal']);


function _probe() {
    return new Promise((resolve) => {
        execFile(
            'nvidia-smi',
            ['--query-gpu=memory.free,memory.total', '--format=csv,noheader,nounits'],
            { timeout: 3000 },
            (err, stdout) => {
                if (err) {
                    // No NVIDIA driver, or nvidia-smi not in PATH — treat as
                    // "free" tier in auto mode. We never fail loud here; the
                    // shell must keep working on AMD/Intel/no-GPU systems.
                    resolve({ available: false, freeMb: 0, totalMb: 0 });
                    return;
                }
                const line = (stdout || '').split('\n')[0] || '';
                const parts = line.split(',').map((s) => Number(s.trim()));
                if (parts.length < 2 || !isFinite(parts[0]) || !isFinite(parts[1])) {
                    resolve({ available: false, freeMb: 0, totalMb: 0 });
                    return;
                }
                resolve({ available: true, freeMb: parts[0], totalMb: parts[1] });
            },
        );
    });
}


class VramTierMonitor extends EventEmitter {
    constructor() {
        super();
        this._mode = 'auto';
        this._lastProbe = null;
        this._lastProbeAt = 0;
        this._lastTier = null;
        this._pollTimer = null;
        // Make sure no listener growth ever crashes the process — we don't
        // expect many subscribers but EventEmitter's default is 10.
        this.setMaxListeners(40);
    }

    setMode(mode) {
        if (!VALID_MODES.has(mode)) {
            throw new Error('Invalid VRAM saver mode: ' + mode);
        }
        if (this._mode === mode) return;
        this._mode = mode;
        // Force a fresh probe so the next getStatus() reflects the new mode
        // immediately instead of returning a stale cached tier.
        this._lastProbeAt = 0;
        // Fire the change synchronously from the renderer's POV. If a poller
        // is running, it'll pick the new tier up at the next tick too.
        this.getStatus().then((st) => {
            if (st.tier !== this._lastTier) {
                this._lastTier = st.tier;
                this.emit('tier-changed', st);
            }
            // Even if the tier didn't change (e.g., off → auto with plenty
            // of VRAM both staying `free`), the renderer wants to know the
            // mode flipped so it can repaint the dropdown subtitle. Fire a
            // separate 'mode-changed' event for that.
            this.emit('mode-changed', st);
        });
    }

    getMode() { return this._mode; }

    async _getProbe(force) {
        if (!force && this._lastProbe && (Date.now() - this._lastProbeAt) < PROBE_TTL_MS) {
            return this._lastProbe;
        }
        this._lastProbe = await _probe();
        this._lastProbeAt = Date.now();
        return this._lastProbe;
    }

    /**
     * Map mode + probe → tier.
     *   off      → free
     *   lean     → lean   (regardless of probe)
     *   minimal  → minimal
     *   auto     → free / lean / minimal based on free VRAM
     * Auto on a non-NVIDIA system collapses to free.
     */
    _classify(probe) {
        if (this._mode === 'off') return 'free';
        if (this._mode === 'lean') return 'lean';
        if (this._mode === 'minimal') return 'minimal';
        // auto
        if (!probe || !probe.available) return 'free';
        if (probe.freeMb < MINIMAL_BELOW_MB) return 'minimal';
        if (probe.freeMb < LEAN_BELOW_MB) return 'lean';
        return 'free';
    }

    /**
     * Returns a snapshot suitable for the renderer:
     *   { tier, mode, freeMb, totalMb, available, label, color }.
     * Color hint is a CSS palette name the renderer can pass through to its
     * own styles without re-classifying.
     */
    async getStatus(force = false) {
        const probe = await this._getProbe(force);
        const tier = this._classify(probe);
        const label = _tierLabel(tier, this._mode, probe);
        const color = { free: 'ok', lean: 'warn', minimal: 'bad' }[tier] || 'ok';
        return {
            tier, mode: this._mode,
            freeMb: probe.freeMb || 0,
            totalMb: probe.totalMb || 0,
            available: !!probe.available,
            label, color,
        };
    }

    /**
     * Force-refresh the tier and fire `tier-changed` if it actually moved.
     * The background poller calls this; renderers can call it via the
     * `vram:status` IPC with `{force:true}` if they really need a fresh read.
     */
    async refresh() {
        const st = await this.getStatus(true);
        if (st.tier !== this._lastTier) {
            this._lastTier = st.tier;
            this.emit('tier-changed', st);
        }
        return st;
    }

    startBackgroundPoll() {
        if (this._pollTimer) return;
        this._pollTimer = setInterval(() => {
            this.refresh().catch(() => { /* probe errors handled inside */ });
        }, SUBSCRIBED_POLL_MS);
        // Don't keep the process alive just for this.
        if (typeof this._pollTimer.unref === 'function') this._pollTimer.unref();
    }

    stopBackgroundPoll() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    }
}


function _tierLabel(tier, mode, probe) {
    const prefix = {
        free: 'Free Savings',
        lean: 'Lean — Low VRAM',
        minimal: 'Minimal — Critical',
    }[tier] || 'Unknown';
    if (mode !== 'auto' && tier !== 'free') {
        return prefix + ' (forced)';
    }
    if (mode === 'auto' && tier === 'free' && probe && !probe.available) {
        return prefix + ' (no NVML)';
    }
    return prefix;
}


module.exports = {
    VramTierMonitor,
    // Re-exported for tests / direct consumers
    _probe,
    _tierLabel,
    MINIMAL_BELOW_MB,
    LEAN_BELOW_MB,
};
