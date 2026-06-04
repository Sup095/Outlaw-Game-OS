// ============================================================================
// Outlaw OS — Local TTS (SC5)
// ----------------------------------------------------------------------------
// CPU-only text-to-speech for the System Core's voice. Two engines in order
// of preference:
//
//   1. piper-tts (https://github.com/rhasspy/piper) — natural-sounding, needs
//      a model file. We pick the first .onnx we find in a handful of common
//      locations or honor $PIPER_MODEL.
//
//   2. espeak-ng — robotic, ships with most distros. Fits the dystopian
//      aesthetic if piper isn't installed.
//
// Both engines are invoked via stdin (text never crosses the shell) so there
// is no command-injection risk. A single concurrent voice — calling speak()
// while a previous line is still playing kills the old one first.
//
// Off-Linux every entry point degrades to a no-op so npm start outside the
// OS doesn't try to spawn anything.
// ============================================================================
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, execFile } = require('child_process');

const IS_LINUX = process.platform === 'linux';
const DETECT_CACHE_MS = 30 * 1000;


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function which(bin) {
    return new Promise((resolve) => {
        if (!IS_LINUX) return resolve(null);
        execFile('command', ['-v', bin], { shell: '/bin/bash' }, (err, out) => {
            resolve(err ? null : (String(out) || '').trim() || null);
        });
    });
}

function findFirst(paths) {
    for (const p of paths) {
        try { if (p && fs.statSync(p).isFile()) return p; } catch { /* skip */ }
    }
    return null;
}

function findPiperModel() {
    const home = os.homedir();
    const candidates = [
        process.env.PIPER_MODEL,
        // Honor an explicit single-file override first, then scan dirs.
    ].filter(Boolean);
    if (candidates.length) {
        const hit = findFirst(candidates);
        if (hit) return hit;
    }
    const dirs = [
        path.join(home, '.local/share/piper'),
        path.join(home, '.local/share/piper-tts'),
        '/usr/local/share/piper',
        '/usr/share/piper',
        '/opt/piper/models',
    ];
    for (const dir of dirs) {
        try {
            const entries = fs.readdirSync(dir).filter((f) => f.endsWith('.onnx'));
            if (entries.length) return path.join(dir, entries[0]);
        } catch { /* dir absent — fine */ }
    }
    return null;
}


// ---------------------------------------------------------------------------
// Engine detection (cached)
// ---------------------------------------------------------------------------

let _engineCache = null;
let _engineCacheAt = 0;

async function detectEngine(force = false) {
    if (!force && _engineCache && (Date.now() - _engineCacheAt < DETECT_CACHE_MS)) {
        return _engineCache;
    }
    let result;
    if (!IS_LINUX) {
        result = { engine: 'none', available: false, note: 'TTS runs on Outlaw OS.' };
    } else {
        const piperBin = await which('piper');
        let piperModel = null;
        if (piperBin) piperModel = findPiperModel();
        if (piperBin && piperModel) {
            result = { engine: 'piper', available: true, path: piperBin, model: piperModel };
        } else {
            const espeakBin = await which('espeak-ng');
            if (espeakBin) {
                result = { engine: 'espeak', available: true, path: espeakBin };
            } else if (piperBin && !piperModel) {
                result = {
                    engine: 'none', available: false,
                    note: 'piper is installed but no .onnx model found. Drop one into ~/.local/share/piper/.',
                };
            } else {
                result = {
                    engine: 'none', available: false,
                    note: 'Install piper-tts (preferred) or espeak-ng via the Apps panel.',
                };
            }
        }
    }
    _engineCache = result;
    _engineCacheAt = Date.now();
    return result;
}

function invalidateEngineCache() {
    _engineCache = null;
    _engineCacheAt = 0;
}


// ---------------------------------------------------------------------------
// Speech orchestration
// ---------------------------------------------------------------------------

// Tracks the currently-speaking pipeline so a new speak() can cancel it.
// Shape: { children: [ChildProcess] }. Empty list means nothing is playing.
let _active = null;

function _killActive() {
    if (!_active) return;
    const procs = _active.children;
    _active = null;
    for (const c of procs) {
        if (!c || c.killed) continue;
        try { c.kill('SIGTERM'); } catch { /* already dead */ }
    }
    // SIGKILL fallback if anything is still alive after a beat.
    setTimeout(() => {
        for (const c of procs) {
            if (c && !c.killed) {
                try { c.kill('SIGKILL'); } catch { /* gone */ }
            }
        }
    }, 500);
}

function cancel() {
    _killActive();
}

async function speak(text) {
    const sanitized = String(text == null ? '' : text).trim();
    if (!sanitized) return { ok: false, error: 'empty text' };

    const eng = await detectEngine();
    if (!eng.available) {
        return { ok: false, error: eng.note || 'no TTS engine available' };
    }

    // Cancel any in-flight speech — single concurrent voice. The previous
    // line is dropped on the floor; we don't queue.
    _killActive();

    if (eng.engine === 'piper') {
        return _speakPiper(sanitized, eng);
    }
    if (eng.engine === 'espeak') {
        return _speakEspeak(sanitized);
    }
    return { ok: false, error: 'unknown engine: ' + eng.engine };
}

function _speakPiper(text, eng) {
    return new Promise((resolve) => {
        // piper writes raw 22050 Hz mono S16LE PCM on stdout; aplay consumes it.
        // Both processes run synchronously in the pipeline; the child of interest
        // for cancellation is aplay (the audible producer) but we kill both.
        const piper = spawn(eng.path, ['--model', eng.model, '--output_raw'], {
            stdio: ['pipe', 'pipe', 'pipe'],
        });
        const aplay = spawn('aplay', ['-q', '-r', '22050', '-c', '1', '-f', 'S16_LE'], {
            stdio: ['pipe', 'inherit', 'pipe'],
        });
        piper.stdout.pipe(aplay.stdin);
        const children = [piper, aplay];
        _active = { children };

        let resolved = false;
        const finish = (ok, err) => {
            if (resolved) return;
            resolved = true;
            if (_active && _active.children === children) _active = null;
            resolve(ok ? { ok: true } : { ok: false, error: err || 'piper failed' });
        };

        piper.on('error', (e) => finish(false, 'piper spawn: ' + e.message));
        aplay.on('error', (e) => finish(false, 'aplay spawn: ' + e.message));
        aplay.on('close', (code) => finish(code === 0));

        // Pipe the text to piper via stdin, then close to signal EOF.
        try {
            piper.stdin.end(text + '\n');
        } catch (e) {
            finish(false, 'piper stdin: ' + e.message);
        }
    });
}

function _speakEspeak(text) {
    return new Promise((resolve) => {
        // -v en+m3   slow male voice (sounds suitably dystopian)
        // -s 155     a touch slower than default (175) so the lines land
        // -p 35      slightly lower pitch
        const child = spawn('espeak-ng', ['-v', 'en+m3', '-s', '155', '-p', '35'], {
            stdio: ['pipe', 'inherit', 'inherit'],
        });
        _active = { children: [child] };

        let resolved = false;
        const finish = (ok, err) => {
            if (resolved) return;
            resolved = true;
            if (_active && _active.children[0] === child) _active = null;
            resolve(ok ? { ok: true } : { ok: false, error: err || 'espeak failed' });
        };

        child.on('error', (e) => finish(false, 'espeak-ng spawn: ' + e.message));
        child.on('close', (code) => finish(code === 0));

        try {
            child.stdin.end(text + '\n');
        } catch (e) {
            finish(false, 'espeak-ng stdin: ' + e.message);
        }
    });
}


module.exports = {
    detectEngine,
    invalidateEngineCache,
    speak,
    cancel,
};
