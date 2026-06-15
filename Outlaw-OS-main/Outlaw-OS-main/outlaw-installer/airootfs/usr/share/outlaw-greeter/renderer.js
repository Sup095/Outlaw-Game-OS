// ============================================================================
// Outlaw OS — Session greeter (renderer)
// CSP-safe: no inline handlers. Click/key handlers wire up to the chooser.
// Phase 8: a skippable cinematic boot intro + an optional "check this PC"
// pre-flight. Both are heavily guarded — the session chooser is ALWAYS reachable
// (a hard timeout reveals it no matter what, and any key/click skips the intro).
// ============================================================================
'use strict';

const api = window.greeter;
let introActive = !!document.getElementById('boot-intro');

const _esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

function endIntro() {
    if (!introActive) return;
    introActive = false;
    const intro = document.getElementById('boot-intro');
    if (intro) intro.classList.add('hide');
    const first = document.querySelector('[data-choice="dev"]');
    if (first) first.focus();
}

async function runIntro() {
    // Safety net #1: reveal the chooser within ~3.2s no matter what happens below.
    const cap = setTimeout(endIntro, 3200);
    const log = document.getElementById('bi-log');
    const sigil = document.getElementById('bi-sigil');
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const push = (s) => { if (log) { log.textContent += s + '\n'; log.scrollTop = log.scrollHeight; } };
    try {
        push('OUTLAW OS · BOOT');
        let lines = [];
        try { lines = (api && api.bootLog) ? await api.bootLog() : []; } catch {}
        const half = Math.ceil(lines.length / 2);
        for (const l of lines.slice(0, half)) { if (!introActive) break; push(l); await sleep(55); }
        if (sigil) sigil.classList.add('warm');
        push('· · · power-on self test · · ·');
        await sleep(800);
        for (const l of lines.slice(half)) { if (!introActive) break; push(l); await sleep(55); }
        push('system ready.');
    } catch { /* ignore — the cap below still reveals the chooser */ }
    clearTimeout(cap);
    setTimeout(endIntro, 450);   // brief beat, then reveal
}

function choose(choice) {
    if (!api) return;  // running outside Electron — preview mode
    const rememberEl = document.getElementById('remember-choice');
    const remember = !!(rememberEl && rememberEl.checked);
    document.body.style.pointerEvents = 'none';
    document.body.style.opacity = '0.55';
    api.choose(choice, remember).catch((err) => console.error('greeter choose failed:', err));
}

async function showPreflight() {
    const out = document.getElementById('preflight-out');
    if (!out) return;
    out.classList.add('show');
    out.textContent = 'Checking this PC…';
    try {
        const r = (api && api.preflight) ? await api.preflight() : null;
        if (!r || !r.ok) { out.textContent = 'Pre-flight check unavailable.'; return; }
        let html = `<b>CPU</b> ${_esc(r.cpu)} (${r.cores} cores) · <b>RAM</b> ${r.ramGb} GB`
            + ` · <b>GPU</b> ${_esc(r.gpu)}${r.vramGb ? ` (${r.vramGb} GB VRAM)` : ''}`;
        if (r.diskFree) html += ` · <b>Free disk</b> ${_esc(r.diskFree)}`;
        html += (r.warnings && r.warnings.length)
            ? '<br>' + r.warnings.map((w) => `<span class="warn">⚠ ${_esc(w)}</span>`).join('<br>')
            : '<br>Looks good — you’re ready to go.';
        out.innerHTML = html;
    } catch { out.textContent = 'Pre-flight check failed.'; }
}

document.addEventListener('click', (e) => {
    if (introActive) { endIntro(); return; }   // first click skips the intro
    if (e.target.closest('[data-action="preflight"]')) { showPreflight(); return; }
    const el = e.target.closest('[data-choice]');
    if (el) choose(el.dataset.choice);
});

document.addEventListener('keydown', (e) => {
    if (introActive) { endIntro(); return; }   // first key skips the intro
    const k = e.key.toLowerCase();
    if (k === '1' || k === 'd') { choose('dev'); return; }
    if (k === '2' || k === 's') { choose('desktop'); return; }
    if (k === 't') { choose('tty'); return; }
    if (e.key === 'Enter') {
        const focused = document.activeElement;
        if (focused && focused.dataset && focused.dataset.choice) choose(focused.dataset.choice);
        else choose('dev');
    }
});

// Safety net #2: even if runIntro throws synchronously, never leave the chooser
// hidden behind the overlay.
setTimeout(endIntro, 3500);

if (introActive) runIntro();
