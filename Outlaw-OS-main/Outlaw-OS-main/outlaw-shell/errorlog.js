// ============================================================================
// Outlaw OS — combined error/warning log (F1).
// ----------------------------------------------------------------------------
// ONE log for the whole system — the desktop shell, the dev session (CodeMaker
// appends to the SAME file), Xorg/session stderr, and the journal. Records only
// errors + warnings, persists across sessions, and DEDUPES (the same line is
// never stored twice). Designed to be downloaded or turned into a prefilled
// GitHub issue so the maintainer can see real failures without the user typing
// them out. Best-effort throughout — logging must never itself crash anything.
// ============================================================================
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFile } = require('child_process');

// $HOME so it survives updates AND is shared by both sessions (CodeMaker writes
// here too). Both sessions run as the same user.
const LOG_PATH = path.join(os.homedir(), '.outlaw-errors.log');
const MAX_LINES = 3000;

const _seen = new Set();

function _hash(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) | 0;
    return h;
}

// Seed the dedup set from whatever is already on disk so restarts don't re-add.
(function loadSeen() {
    try {
        const txt = fs.readFileSync(LOG_PATH, 'utf8');
        for (const line of txt.split('\n')) {
            const body = line.replace(/^\[[^\]]*\]\s*/, '').trim();
            if (body) _seen.add(_hash(body));
        }
    } catch { /* no file yet */ }
})();

function _trim() {
    try {
        const lines = fs.readFileSync(LOG_PATH, 'utf8').split('\n');
        if (lines.length > MAX_LINES + 200) {
            fs.writeFileSync(LOG_PATH, lines.slice(-MAX_LINES).join('\n'));
        }
    } catch { /* ignore */ }
}

// Append one error/warning. level e.g. 'error'|'warn'; source e.g. 'shell-main'.
function append(level, source, message) {
    try {
        const msg = String(message == null ? '' : message).replace(/\s+/g, ' ').trim().slice(0, 600);
        if (!msg) return;
        const body = `${source}: ${msg}`;
        const key = _hash(body);
        if (_seen.has(key)) return;   // dedup — never the same entry twice
        _seen.add(key);
        fs.appendFileSync(LOG_PATH, `[${new Date().toISOString()}] ${String(level).toUpperCase()} ${body}\n`);
        _trim();
    } catch { /* logging must never throw */ }
}

// Scrape error/warning lines out of the X/session log + the journal into the
// combined log (so a crash that bounced the user to the picker is captured).
function collect() {
    // Session stderr (the .xinitrc redirects Electron/X stderr here).
    try {
        const xlog = path.join(os.homedir(), '.outlaw-x.log');
        for (const line of fs.readFileSync(xlog, 'utf8').split('\n')) {
            if (/\b(error|fail|failed|fatal|cannot|unable|segfault|traceback|warn|denied|refused)\b/i.test(line)) {
                append('session', 'xorg', line);
            }
        }
    } catch { /* no x log */ }
    return new Promise((resolve) => {
        try {
            execFile('journalctl', ['-b', '-p', 'warning', '--no-pager', '-n', '250', '-o', 'cat'],
                { timeout: 6000 }, (_e, out) => {
                    if (out) for (const line of String(out).split('\n')) {
                        if (line.trim()) append('journal', 'system', line);
                    }
                    resolve(read());
                });
        } catch { resolve(read()); }
    });
}

function read() {
    try { return fs.readFileSync(LOG_PATH, 'utf8'); } catch { return ''; }
}

function clear() {
    try { fs.writeFileSync(LOG_PATH, ''); _seen.clear(); } catch { /* ignore */ }
}

// Build a prefilled GitHub "new issue" URL from the most recent log lines. No
// token needed — it just opens the issue form in the browser with the body
// filled in. `repo` is "owner/name".
function issueUrl(repo, version) {
    const tail = read().split('\n').slice(-120).join('\n').slice(-5500);
    const title = `Outlaw OS error report (v${version || '?'})`;
    const body = `**Auto-collected error/warning log** (v${version || '?'}):\n\n\`\`\`\n${tail || '(empty)'}\n\`\`\`\n`;
    const base = `https://github.com/${repo || 'Sup095/Outlaw-Game-OS'}/issues/new`;
    return `${base}?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
}

module.exports = { append, collect, read, clear, issueUrl, LOG_PATH };
