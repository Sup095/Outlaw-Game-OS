// ============================================================================
// Outlaw OS — Session greeter (renderer)
// CSP-safe: no inline handlers. Click/key handlers wire up to the chooser.
// ============================================================================
'use strict';

const api = window.greeter;

function choose(choice) {
    if (!api) return;  // running outside Electron — preview mode
    const rememberEl = document.getElementById('remember-choice');
    const remember = !!(rememberEl && rememberEl.checked);
    document.body.style.pointerEvents = 'none';
    document.body.style.opacity = '0.55';
    api.choose(choice, remember).catch((err) => console.error('greeter choose failed:', err));
}

document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-choice]');
    if (el) choose(el.dataset.choice);
});

// Keyboard niceties:
//   1 / D → Dev session
//   2 / S → Desktop
//   T     → drop to terminal
//   ENTER → activate the focused card (default = Dev)
document.addEventListener('keydown', (e) => {
    const k = e.key.toLowerCase();
    if (k === '1' || k === 'd') { choose('dev'); return; }
    if (k === '2' || k === 's') { choose('desktop'); return; }
    if (k === 't') { choose('tty'); return; }
    if (e.key === 'Enter') {
        const focused = document.activeElement;
        if (focused && focused.dataset && focused.dataset.choice) {
            choose(focused.dataset.choice);
        } else {
            choose('dev');
        }
    }
});
