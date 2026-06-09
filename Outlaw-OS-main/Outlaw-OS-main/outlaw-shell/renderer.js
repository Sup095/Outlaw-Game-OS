// ============================================================================
// Outlaw OS - renderer
// No inline handlers (CSP-safe). Everything talks to the main process through
// the audited `window.outlaw` bridge defined in preload.js.
// ============================================================================
'use strict';

const api = window.outlaw;
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// The sponsor URL is configurable in Settings → Support Development.

let statsTimer = null;
let confirmResolver = null;
let pendingUpdate = null;   // most recent successful shell-update check result

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
let toastTimer = null;
function toast(msg) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}

// ---------------------------------------------------------------------------
// Boot sequence
// ---------------------------------------------------------------------------
async function runBoot() {
    const log = $('#boot-log');
    const lines = ['INITIALIZING OUTLAW OS…'];
    log.textContent = lines.join('\n') + '\n';
    try {
        const i = await api.system.info();
        lines.push(`HOST     ${i.hostname}`);
        lines.push(`KERNEL   ${i.kernel}`);
        lines.push(`CPU      ${i.cpu} (${i.cores} cores)`);
        lines.push(`MEMORY   ${i.ramUsed} / ${i.ramTotal}`);
    } catch {
        lines.push('SYSTEM PROBE UNAVAILABLE (preview mode)');
    }
    lines.push('MOUNTING PAYLOAD VAULT… OK');
    lines.push('SECURITY GUARD ACTIVE… OK');
    lines.push('SYSTEM READY.');
    // Type the new lines out.
    for (let n = 5; n < lines.length; n++) {
        log.textContent = lines.slice(0, n + 1).join('\n') + '\n';
        await new Promise((r) => setTimeout(r, 120));
    }
    $('#boot-skip').focus();
}

function enterOS() {
    $('#boot').style.display = 'none';
    $('#app').classList.add('ready');
    startStats();
    refreshAiStatus();
    checkSafeMode();
}

// Show a persistent toast banner if outlaw-session-watchdog flipped us into
// safe mode after a crash loop. The IPC consumes the marker, so the banner
// fires once per X session.
async function checkSafeMode() {
    try {
        const r = await api.safeMode.check();
        if (r && r.active) {
            const reason = r.reason || 'A previous session was crash-looping.';
            // Toast for ~12 seconds — longer than a normal toast since the
            // user really should see this.
            const t = $('#toast');
            if (t) {
                t.textContent = '⚠ Safe mode: ' + reason.slice(0, 160);
                t.classList.add('show');
                setTimeout(() => t.classList.remove('show'), 12_000);
            }
        }
    } catch {
        /* shell still works without this */
    }
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
function showScreen(name) {
    $$('.screen').forEach((s) => s.classList.remove('active'));
    const el = $('#screen-' + name);
    if (el) el.classList.add('active');
    $$('.nav-item[data-screen]').forEach((n) => n.classList.toggle('active', n.dataset.screen === name));
    if (name === 'files') loadFiles(currentDir || null);
    if (name === 'tasks') refreshTasks();
    if (name === 'gaming') refreshGaming();
    if (name === 'apps') loadAppsCatalog();
    if (name === 'ai') $('#ai-in').focus();
    if (name === 'terminal') $('#term-in').focus();
    // System Core lifecycle — init when navigating to it, teardown otherwise.
    // The module is self-contained so this is the only hook the rest of the
    // shell needs to know about. SC2+ slices plug into the same init/teardown.
    if (window.outlawCore) {
        if (name === 'syscore') window.outlawCore.init();
        else window.outlawCore.teardown();
    }
}

// ---------------------------------------------------------------------------
// Dashboard + app tiles
// ---------------------------------------------------------------------------
// Tiles rendered on each screen. A tile that maps to a not-yet-installed app
// still appears — clicking it toasts "X is not installed" and the user can
// pop over to Apps to install it. Tiles that can NEVER be installed from
// official repos (e.g. Heroic — AUR only) are intentionally omitted.
const TILE_GROUPS = {
    launchers: [['browser', '🌐'], ['steam', '🎮'], ['godot', '🤖'], ['files', '📁'], ['terminal', '>_']],
    'gaming-apps': [['steam', '🎮'], ['lutris', '🍷']],
    'gamedev-apps': [['godot', '🤖'], ['blender', '🧊'], ['gimp', '🎨'], ['code', '💻']],
};

async function renderTiles() {
    let registry = [];
    try { registry = await api.apps.list(); } catch {}
    const labelOf = (id) => (registry.find((r) => r.id === id) || {}).label || id;
    for (const [containerId, items] of Object.entries(TILE_GROUPS)) {
        const box = document.getElementById(containerId);
        if (!box) continue;
        box.innerHTML = '';
        for (const [id, ico] of items) {
            const b = document.createElement('button');
            b.className = 'tile';
            b.dataset.launch = id;
            b.innerHTML = `<span class="t-ico">${ico}</span><span class="t-label">${labelOf(id)}</span><span class="t-sub">launch</span>`;
            box.appendChild(b);
        }
    }
}

// ---------------------------------------------------------------------------
// Apps panel (on-demand installer over the curated catalog)
// ---------------------------------------------------------------------------
const CATEGORY_ICONS = {
    'Game Dev':     '🛠',
    'Gaming':       '🎮',
    'Browsers':     '🌐',
    'Productivity': '📑',
    'Security':     '🛡',
};

// Built once, then mutated in-place when install state changes. `filter` is
// either a category name from the catalog ("Game Dev", "Security", …),
// the special tokens "all" / "installed", or an empty search string.
let _appsState = {
    catalog: [],
    installed: new Set(),
    busy: new Set(),
    filter: 'all',
    search: '',
    discovered: [],   // Phase 2 — apps found on this PC (.desktop + AppImages)
};

function _escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _renderDiscoveredList(root) {
    // Phase 2 — apps already on this PC (installed .desktop entries + AppImages
    // the user downloaded). These are already installed, so the only action is
    // Launch (the no-WM focus fix makes the launched window actually focusable).
    const q = (_appsState.search || '').trim().toLowerCase();
    let list = _appsState.discovered || [];
    if (q) list = list.filter((a) => (a.name || '').toLowerCase().includes(q));
    if (!list.length) {
        root.innerHTML = '<div class="muted" style="padding:24px;text-align:center;">'
            + 'Nothing found on this PC yet. Install something from the catalog, or drop an '
            + 'AppImage in your <b>Downloads</b> folder — it shows up here automatically.</div>';
        return;
    }
    const html = [
        `<h3 style="margin-top:18px;">💾  On this PC <span class="muted" style="font-weight:400;">(${list.length})</span></h3>`,
        '<div class="grid cols-2">',
    ];
    for (const a of list) {
        const tag = a.kind === 'appimage' ? 'AppImage' : 'Installed app';
        html.push(`
            <div class="card">
                <div class="row" style="align-items:flex-start;gap:10px;">
                    <div style="flex:1;min-width:0;">
                        <div style="font-weight:600;word-break:break-word;">${_escapeHtml(a.name)}</div>
                        <div class="muted" style="font-size:11px;margin-top:3px;">${tag}</div>
                    </div>
                    <div class="row" style="gap:6px;flex:0 0 auto;">
                        <button data-launch-disc="${_escapeHtml(a.id)}">Launch</button>
                    </div>
                </div>
            </div>
        `);
    }
    html.push('</div>');
    root.innerHTML = html.join('');
}

function _renderAppsList() {
    const root = $('#apps-list');
    if (!root) return;
    if (_appsState.filter === 'discovered') { _renderDiscoveredList(root); return; }
    const { catalog, installed, busy, filter, search } = _appsState;
    if (!catalog.length) {
        root.innerHTML = '<div class="muted" style="padding:24px;text-align:center;">No apps in catalog.</div>';
        return;
    }
    const q = (search || '').trim().toLowerCase();
    const matches = (a) => {
        if (filter === 'installed' && !installed.has(a.id)) return false;
        if (filter !== 'all' && filter !== 'installed' && a.category !== filter) return false;
        if (!q) return true;
        return (a.label + ' ' + a.description).toLowerCase().includes(q);
    };
    const filtered = catalog.filter(matches);
    if (!filtered.length) {
        root.innerHTML = '<div class="muted" style="padding:24px;text-align:center;">' +
            'No matches. Try a different filter or clear the search.</div>';
        return;
    }
    const byCat = new Map();
    for (const a of filtered) {
        if (!byCat.has(a.category)) byCat.set(a.category, []);
        byCat.get(a.category).push(a);
    }
    const html = [];
    for (const [category, apps] of byCat) {
        const icon = CATEGORY_ICONS[category] || '📦';
        html.push(`<h3 style="margin-top:18px;">${icon}  ${_escapeHtml(category)}</h3>`);
        html.push('<div class="grid cols-2">');
        for (const a of apps) {
            const isInstalled = installed.has(a.id);
            const isBusy = busy.has(a.id);
            const btnLabel = isBusy
                ? (isInstalled ? 'Removing…' : 'Installing…')
                : (isInstalled ? 'Uninstall' : 'Install');
            const btnClass = isInstalled ? 'danger' : 'primary';
            const dataAttr = isInstalled
                ? `data-uninstall-id="${_escapeHtml(a.id)}"`
                : `data-install-id="${_escapeHtml(a.id)}"`;
            const launchBtn = (isInstalled && a.launchable)
                ? `<button data-launch="${_escapeHtml(a.id)}">Launch</button>`
                : '';
            html.push(`
                <div class="card">
                    <div class="row" style="align-items:flex-start;gap:10px;">
                        <div style="flex:1;min-width:0;">
                            <div style="font-weight:600;">${_escapeHtml(a.label)}</div>
                            <div class="muted" style="font-size:11px;margin-top:3px;">${_escapeHtml(a.description)}</div>
                        </div>
                        <div class="row" style="gap:6px;flex:0 0 auto;">
                            ${launchBtn}
                            <button class="${btnClass}" ${isBusy ? 'disabled' : ''} ${dataAttr}>${btnLabel}</button>
                        </div>
                    </div>
                </div>
            `);
        }
        html.push('</div>');
    }
    root.innerHTML = html.join('');
}

function setAppsFilter(filter) {
    _appsState.filter = filter || 'all';
    document.querySelectorAll('[data-apps-filter]').forEach((el) => {
        el.classList.toggle('active', el.dataset.appsFilter === _appsState.filter);
    });
    _renderAppsList();
}

function setAppsSearch(q) {
    _appsState.search = q || '';
    _renderAppsList();
}

async function loadAppsCatalog() {
    try {
        const [catalog, installedList, discovered] = await Promise.all([
            api.apps.catalog(),
            api.apps.installedList(),
            api.apps.discover(),
        ]);
        _appsState.catalog = catalog || [];
        _appsState.installed = new Set((installedList || []).filter((x) => x.installed).map((x) => x.id));
        _appsState.discovered = discovered || [];
    } catch {
        _appsState.catalog = [];
        _appsState.installed = new Set();
        _appsState.discovered = [];
    }
    _renderAppsList();
}

async function refreshAppsInstalledOnly() {
    // Cheaper refresh — only the install-state set, used after install/uninstall.
    try {
        const list = await api.apps.installedList();
        _appsState.installed = new Set((list || []).filter((x) => x.installed).map((x) => x.id));
    } catch {}
    _renderAppsList();
}

async function handleAppsInstall(id) {
    const app = _appsState.catalog.find((a) => a.id === id);
    if (!app) return;
    _appsState.busy.add(id);
    _renderAppsList();
    toast(`Installing ${app.label}… you may see a password prompt.`);
    try {
        const r = await api.apps.install(id);
        if (r.ok) {
            toast(`${app.label} installed.`);
        } else {
            toast(`Install failed: ${(r.error || '').split('\n')[0].slice(0, 140) || 'unknown error'}`);
        }
    } catch (e) {
        toast(`Install failed: ${e.message}`);
    }
    _appsState.busy.delete(id);
    await refreshAppsInstalledOnly();
}

async function handleAppsUninstall(id) {
    const app = _appsState.catalog.find((a) => a.id === id);
    if (!app) return;
    const ok = window.confirm(
        `Uninstall ${app.label}?\n\n` +
        `This removes the package and any of its dependencies that nothing else needs. ` +
        `You can reinstall it later from the Apps panel.`,
    );
    if (!ok) return;
    _appsState.busy.add(id);
    _renderAppsList();
    try {
        const r = await api.apps.uninstall(id);
        if (r.ok) {
            toast(`${app.label} removed.`);
        } else {
            toast(`Uninstall failed: ${(r.error || '').split('\n')[0].slice(0, 140) || 'unknown error'}`);
        }
    } catch (e) {
        toast(`Uninstall failed: ${e.message}`);
    }
    _appsState.busy.delete(id);
    await refreshAppsInstalledOnly();
}

async function launchDiscoveredApp(id) {
    try {
        const r = await api.apps.launchDiscovered(id);
        toast(r && r.ok ? `Launching ${r.label}…` : (r && r.error ? r.error : 'Could not launch.'));
    } catch (e) {
        toast('Launch failed: ' + e.message);
    }
}

async function loadSysInfo() {
    try {
        const i = await api.system.info();
        const gpu = await api.system.gpu();
        $('#sysinfo').textContent =
            `${i.hostname}  •  ${i.cpu} (${i.cores} cores)\nRAM ${i.ramUsed}/${i.ramTotal}  •  kernel ${i.kernel}\nGPU ${gpu}`;
        const v = $('#app-version'); if (v) v.textContent = 'v' + (i.appVersion || '?');
    } catch {
        $('#sysinfo').textContent = 'Preview mode — full telemetry available on Outlaw OS.';
    }
}

async function checkShellUpdate() {
    const status = $('#shell-update-status');
    const btn = $('#install-shell-btn');
    status.textContent = 'checking GitHub…';
    btn.disabled = true;
    const r = await api.updates.checkShell();
    if (!r.ok) { status.textContent = r.error; pendingUpdate = null; return; }
    if (!r.available) {
        status.textContent = `up to date (v${r.currentVersion})`;
        pendingUpdate = null;
        return;
    }
    pendingUpdate = r;
    status.textContent = `v${r.remoteVersion} available (you have v${r.currentVersion})`;
    btn.disabled = false;
}

async function installShellUpdate() {
    if (!pendingUpdate || !pendingUpdate.assetUrl) { toast('Run "Check for shell updates" first.'); return; }
    const status = $('#shell-update-status');
    status.textContent = 'downloading + verifying…';
    $('#install-shell-btn').disabled = true;
    const r = await api.updates.installShell({
        assetUrl: pendingUpdate.assetUrl,
        shaUrl: pendingUpdate.shaUrl,
    });
    if (!r.ok) {
        status.textContent = r.error;
        $('#install-shell-btn').disabled = false;
        return;
    }
    status.textContent = 'installed — restart the shell to load it.';
    toast('Update installed. Restart the shell to finish.');
    pendingUpdate = null;
    // A successful update leaves a .prev behind; flip the Rollback button on.
    refreshRollbackAvailability();
}

// Probe whether /usr/share/outlaw-os.prev exists so the Rollback button is
// only enabled when there's actually something to roll back to.
async function refreshRollbackAvailability() {
    const btn = $('#rollback-shell-btn');
    const status = $('#rollback-status');
    if (!btn) return;
    try {
        const r = await api.updates.checkRollback();
        btn.disabled = !r.available;
        if (status) status.textContent = r.available ? '' : (r.note || 'no previous version on disk');
    } catch (err) {
        btn.disabled = true;
        if (status) status.textContent = 'check failed';
    }
}

async function rollbackShell() {
    const btn = $('#rollback-shell-btn');
    const status = $('#rollback-status');
    if (!btn || btn.disabled) return;
    const ok = window.confirm(
        'Roll back to the previous Outlaw shell?\n\n' +
        'This swaps /usr/share/outlaw-os with /usr/share/outlaw-os.prev. ' +
        'You can roll forward again by clicking the same button after the swap.\n\n' +
        'You\'ll need to restart the shell (or reboot) to see the change.',
    );
    if (!ok) return;
    btn.disabled = true;
    if (status) status.textContent = 'rolling back — enter your password if prompted…';
    try {
        const r = await api.updates.rollback();
        if (!r.ok) {
            if (status) status.textContent = r.error || 'rollback failed';
            btn.disabled = false;
            return;
        }
        if (status) status.textContent = 'rolled back — restart the shell to load it.';
        toast('Rolled back. Restart the shell to finish.');
    } catch (err) {
        if (status) status.textContent = 'rollback failed: ' + err.message;
        btn.disabled = false;
    }
    refreshRollbackAvailability();
}

async function launchApp(id) {
    const r = await api.apps.launch(id);
    toast(r.ok ? `Launching ${r.label}…` : (r.error || 'Could not launch.'));
}

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------
let currentDir = null;
let parentDir = null;
async function loadFiles(dir) {
    const res = await api.files.list(dir);
    if (res.error) { toast(res.error); }
    currentDir = res.path; parentDir = res.parent;
    $('#fs-path').textContent = res.path;
    const list = $('#fs-list');
    list.innerHTML = '';
    if (!res.entries || !res.entries.length) {
        list.innerHTML = '<div class="muted" style="padding:10px;">(empty or unreadable)</div>';
        return;
    }
    for (const e of res.entries) {
        const row = document.createElement('button');
        row.className = 'fs-row';
        row.dataset.name = e.name;
        row.dataset.type = e.type;
        const ico = e.type === 'dir' ? '📁' : '📄';
        const size = e.type === 'file' ? humanSize(e.size) : '';
        row.innerHTML = `<span>${ico}</span><span>${escapeHtml(e.name)}</span><span class="sz">${size}</span>`;
        list.appendChild(row);
    }
}
function humanSize(b) {
    if (!b) return '';
    const u = ['B', 'K', 'M', 'G']; let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return b.toFixed(b < 10 && i > 0 ? 1 : 0) + u[i];
}

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------
async function refreshTasks() {
    const procs = await api.system.processes();
    const body = $('#proc-body');
    body.innerHTML = '';
    for (const p of procs) {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${p.pid}</td><td>${escapeHtml(p.comm)}</td><td class="right">${p.cpu}</td><td class="right">${p.mem}</td>`;
        body.appendChild(tr);
    }
    updateBars();
}
async function updateBars() {
    const s = await api.system.stats();
    $('#cpu-bar').style.width = Math.min(100, s.cpu).toFixed(0) + '%';
    $('#ram-bar').style.width = Math.min(100, s.ramPct).toFixed(0) + '%';
    $('#cpu-val').textContent = s.cpu.toFixed(0) + '%';
    $('#ram-val').textContent = `${s.ramUsed} / ${s.ramTotal} (${s.ramPct.toFixed(0)}%)`;
}

// ---------------------------------------------------------------------------
// Live top-bar stats
// ---------------------------------------------------------------------------
function startStats() {
    const tick = async () => {
        try {
            const s = await api.system.stats();
            $('#stat-cpu').textContent = `CPU ${s.cpu.toFixed(0)}%`;
            $('#stat-ram').textContent = `RAM ${s.ramUsed}`;
            $('#stat-clock').textContent = s.time;
        } catch {}
    };
    tick();
    statsTimer = setInterval(tick, 2000);
}

// ---------------------------------------------------------------------------
// Terminal (guarded)
// ---------------------------------------------------------------------------
async function inspectCommand(cmd) {
    const hint = $('#term-hint');
    if (!cmd.trim()) { hint.textContent = ''; hint.classList.remove('warn'); return; }
    try {
        const c = await api.terminal.inspect(cmd);
        if (c.danger) { hint.textContent = '⚠ ' + c.reason + ' — confirmation required.'; hint.classList.add('warn'); }
        else { hint.textContent = ''; hint.classList.remove('warn'); }
    } catch {}
}

async function runTerminal(cmd) {
    const out = $('#term-out');
    out.value += `> ${cmd}\n`;
    const c = await api.terminal.inspect(cmd);
    let confirmDangerous = false;
    if (c.danger) {
        const ok = await askConfirm({ title: 'Dangerous command', reason: c.reason, cmd });
        if (!ok) { out.value += '(cancelled)\n\n'; out.scrollTop = out.scrollHeight; return; }
        confirmDangerous = true;
    }
    const r = await api.terminal.run(cmd, { confirmDangerous });
    if (r.blocked) out.value += `[blocked] ${r.reason}\n\n`;
    else out.value += `${(r.stdout || r.stderr || `(exit ${r.code})`)}\n\n`;
    out.scrollTop = out.scrollHeight;
}

// ---------------------------------------------------------------------------
// Confirm modal (shared by terminal + AI run_command)
// ---------------------------------------------------------------------------
function askConfirm({ title, reason, cmd }) {
    $('#confirm-title').textContent = title || 'Confirm dangerous action';
    $('#confirm-reason').textContent = reason || '';
    $('#confirm-cmd').textContent = cmd || '';
    $('#confirm-input').value = '';
    $('#confirm-go').disabled = true;
    $('#confirm-modal').classList.add('show');
    $('#confirm-input').focus();
    return new Promise((resolve) => { confirmResolver = resolve; });
}
function closeConfirm(result) {
    $('#confirm-modal').classList.remove('show');
    if (confirmResolver) { confirmResolver(result); confirmResolver = null; }
}

// ---------------------------------------------------------------------------
// AI chat
// ---------------------------------------------------------------------------
function addMsg(kind, text) {
    const log = $('#ai-log');
    const div = document.createElement('div');
    div.className = 'msg ' + kind;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

async function sendAI() {
    const input = $('#ai-in');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    addMsg('user', text);
    const thinking = document.createElement('div');
    thinking.className = 'msg ai'; thinking.textContent = '…';
    $('#ai-log').appendChild(thinking);
    const res = await api.ai.ask(text);
    thinking.remove();
    if (res.error) { addMsg('sys', res.error); return; }
    if (res.needsConfirm) {
        addMsg('ai', res.text);
        const danger = res.classify && res.classify.danger;
        const ok = await askConfirm({
            title: danger ? 'AI wants to run a dangerous command' : 'Run this command?',
            reason: danger ? res.classify.reason : 'The assistant proposed a shell command.',
            cmd: res.action.arg,
        });
        if (!ok) { addMsg('sys', 'Cancelled.'); return; }
        const r = await api.ai.confirmAction(res.action);
        addMsg('ai', r.text || '(done)');
        return;
    }
    addMsg('ai', res.text || '(no answer)');
}

async function refreshAiStatus() {
    let s = { enabled: false, available: false };
    try { s = await api.ai.status(); } catch {}
    const pill = $('#stat-ai');
    pill.textContent = 'AI ' + (s.enabled ? (s.available ? 'ON' : 'STARTING') : 'OFF');
    const badge = $('#ai-badge');
    if (badge) {
        badge.textContent = s.enabled ? (s.available ? 'online' : 'starting') : 'offline';
        badge.className = 'badge ' + (s.enabled && s.available ? 'on' : 'off');
    }
    const toggle = $('#ai-toggle');
    if (toggle) toggle.checked = !!s.enabled;
    const sub = $('#ai-sub');
    if (sub) sub.textContent = s.enabled
        ? (s.available
            ? 'Active · routing prompts to LM Studio'
            : 'Waiting for LM Studio — open it, load a model, click Start Server (port 1234).')
        : 'Off · routes prompts to LM Studio on this machine';
    const modelSub = $('#ai-model-sub');
    if (modelSub) {
        if (s.enabled && s.available) {
            const loaded = (s.models && s.models[0]) || s.model || '(no model loaded)';
            modelSub.textContent = 'Loaded in LM Studio: ' + loaded + ' — swap models there.';
        } else {
            modelSub.textContent = 'Loaded in LM Studio — change the model there to swap it everywhere.';
        }
    }
}

// ---------------------------------------------------------------------------
// Calculator (safe expression evaluator — no eval)
// ---------------------------------------------------------------------------
let calcExpr = '';
function calcRender() { $('#calc-display').value = calcExpr || '0'; }
function calcKey(k) {
    if (k === 'C') { calcExpr = ''; calcRender(); return; }
    if (k === '=') {
        try { calcExpr = String(safeEval(calcExpr)); }
        catch { calcExpr = ''; $('#calc-display').value = 'ERROR'; return; }
        calcRender(); return;
    }
    if ('+-*/.'.includes(k)) {
        if (!calcExpr && k !== '-') return;            // don't start with an operator (except minus)
        if (/[+\-*/.]$/.test(calcExpr)) calcExpr = calcExpr.slice(0, -1); // replace trailing operator
    }
    calcExpr += k;
    calcRender();
}
function safeEval(expr) {
    const tokens = (expr.match(/(\d+\.?\d*|\.\d+|[+\-*/()])/g) || []);
    if (tokens.join('') !== expr.replace(/\s+/g, '')) throw new Error('bad');
    // Shunting-yard to RPN
    const out = [], ops = [];
    const prec = { '+': 1, '-': 1, '*': 2, '/': 2, 'u-': 3 };
    let prev = null;
    for (const t of tokens) {
        if (/^[\d.]/.test(t)) { out.push(parseFloat(t)); }
        else if (t === '(') { ops.push(t); }
        else if (t === ')') { while (ops.length && ops[ops.length - 1] !== '(') out.push(ops.pop()); if (!ops.length) throw new Error('paren'); ops.pop(); }
        else {
            let op = t;
            if (t === '-' && (prev === null || prev === '(' || '+-*/'.includes(prev))) op = 'u-';
            while (ops.length && ops[ops.length - 1] !== '(' && prec[ops[ops.length - 1]] >= prec[op]) out.push(ops.pop());
            ops.push(op);
        }
        prev = t;
    }
    while (ops.length) { const o = ops.pop(); if (o === '(') throw new Error('paren'); out.push(o); }
    const st = [];
    for (const tk of out) {
        if (typeof tk === 'number') st.push(tk);
        else if (tk === 'u-') st.push(-st.pop());
        else { const b = st.pop(), a = st.pop(); if (a === undefined || b === undefined) throw new Error('expr');
            st.push(tk === '+' ? a + b : tk === '-' ? a - b : tk === '*' ? a * b : a / b); }
    }
    if (st.length !== 1 || !isFinite(st[0])) throw new Error('expr');
    return Math.round(st[0] * 1e10) / 1e10;
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
// --- P2: stability reporting -------------------------------------------------
// The user's own per-version vote lives in settings.stabilityReports
// ({ "2.0.11": "works" | "broken" }); the community tally is a read-only
// GitHub reaction count fetched on demand (zero idle network cost).
let _stabilityReports = {};
let _stabilityVersion = '';
let _stabilityShareUrl = '';

function _normVer(v) { return String(v || '').replace(/^v/i, '').replace(/-.*$/, '').trim(); }

async function _ensureStabilityVersion() {
    if (_stabilityVersion) return _stabilityVersion;
    try { const i = await api.system.info(); _stabilityVersion = _normVer(i.appVersion); } catch {}
    return _stabilityVersion;
}

async function refreshStabilityUi() {
    await _ensureStabilityVersion();
    const vEl = $('#stability-version');
    if (vEl) vEl.textContent = _stabilityVersion ? ('v' + _stabilityVersion) : 'this build';
    const mine = _stabilityReports[_stabilityVersion];
    const yv = $('#stability-your-vote');
    if (yv) {
        yv.textContent = mine === 'works' ? 'You marked this: Works ✓'
            : mine === 'broken' ? 'You marked this: Problems ✗'
            : '';
    }
}

async function setStabilityVote(vote) {
    await _ensureStabilityVersion();
    if (!_stabilityVersion) { toast('Version unknown — can’t record a report.'); return; }
    _stabilityReports = { ..._stabilityReports, [_stabilityVersion]: vote };
    try { await api.settings.set({ stabilityReports: _stabilityReports }); } catch {}
    refreshStabilityUi();
    toast(vote === 'works' ? 'Thanks! Marked as working.' : 'Noted — thanks for the report.');
    refreshStabilityTally();
}

async function refreshStabilityTally() {
    const el = $('#stability-tally');
    if (el) el.textContent = 'checking…';
    try {
        const t = await api.stability.tally();
        if (!t || !t.ok) { if (el) el.textContent = (t && t.error) ? t.error : 'unavailable'; return; }
        _stabilityShareUrl = t.htmlUrl || '';
        if (el) {
            el.textContent = t.found
                ? `👍 ${t.works}   👎 ${t.broken}   (community)`
                : 'no matching release yet';
        }
    } catch {
        if (el) el.textContent = 'unavailable';
    }
}

function shareStabilityFeedback() {
    const url = _stabilityShareUrl || '';
    if (/^https?:\/\//i.test(url)) { window.open(url, '_blank'); return; }
    // Fall back to the repo's releases page if we haven't fetched a tally yet.
    refreshStabilityTally().then(() => {
        if (/^https?:\/\//i.test(_stabilityShareUrl)) window.open(_stabilityShareUrl, '_blank');
        else toast('Set the GitHub repository in Settings first.');
    });
}

// --- P5: per-machine hardware tuning ----------------------------------------
let _tuneRec = null;

function _fmtMB(mb) {
    if (mb == null || mb < 0) return 'n/a';
    return mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb + ' MB';
}

function _renderTune(p, r) {
    const L = [];
    L.push('HARDWARE');
    L.push('  CPU      : ' + (p.cpu_model || '?') + '  (' + p.cpu_cores + ' cores)');
    L.push('  Memory   : ' + _fmtMB(p.ram_mb) + ' RAM, ' + _fmtMB(p.swap_mb) + ' swap');
    L.push('  Disk     : ' + (p.root_rotational ? 'HDD (spinning)' : 'SSD / NVMe'));
    L.push('  GPU      : ' + (p.gpu || '?') + (p.vram_mb >= 0 ? '  (' + _fmtMB(p.vram_mb) + ' VRAM)' : ''));
    L.push('  Platform : ' + (p.virt && p.virt !== 'none' ? ('VM — ' + p.virt) : 'bare metal') + (p.is_laptop ? ', laptop' : ''));
    if (p.temp_c >= 0) L.push('  Temp     : ' + p.temp_c + '°C');
    if (r) {
        L.push('');
        L.push('RECOMMENDED FOR THIS MACHINE');
        L.push('  CPU governor     : ' + r.governor);
        L.push('  Swappiness       : ' + r.swappiness);
        L.push('  zram swap        : ' + (r.zram_mb > 0 ? _fmtMB(r.zram_mb) : 'off'));
        L.push('  File-watch limit : ' + r.inotify_watches);
        L.push('  Max map count    : ' + r.max_map_count);
        L.push('  CodeMaker VRAM   : ' + r.vram_mode);
    }
    return L.join('\n');
}

async function tuneScan() {
    const out = $('#tune-output'), st = $('#tune-status');
    st.textContent = 'scanning…'; out.style.display = 'block'; out.textContent = 'Reading hardware…';
    try {
        const p = await api.tune.probe();
        if (!p || !p.ok) { st.textContent = ''; out.textContent = (p && p.error) || 'probe failed'; return; }
        const r = await api.tune.recommend();
        _tuneRec = (r && r.ok) ? r.data : null;
        out.textContent = _renderTune(p.data, _tuneRec);
        st.textContent = 'scan complete';
        const btn = $('#tune-apply-btn'); if (btn) btn.disabled = !_tuneRec;
    } catch (e) { st.textContent = ''; out.textContent = 'error: ' + e.message; }
}

async function tuneStress() {
    const out = $('#tune-output'), st = $('#tune-status');
    if (!window.confirm('Run a ~10-second CPU stress test? This briefly loads all cores while watching temperature.')) return;
    st.textContent = 'stress testing…'; out.style.display = 'block'; out.textContent = 'Loading all CPU cores for ~10 seconds…';
    try {
        const r = await api.tune.stress(10);
        if (!r || !r.ok) { st.textContent = ''; out.textContent = (r && r.error) || 'stress test failed'; return; }
        const d = r.data, L = [];
        L.push('STRESS TEST');
        L.push('  Cores loaded : ' + d.cores_stressed + ' for ' + d.seconds + 's');
        L.push('  CPU score    : ' + d.score_kops + ' k-ops/s (single core, relative)');
        if (d.temp_before_c >= 0) L.push('  Temp before  : ' + d.temp_before_c + '°C');
        if (d.temp_peak_c >= 0) L.push('  Temp peak    : ' + d.temp_peak_c + '°C');
        L.push('  Thermals     : ' + (d.thermal_ok ? 'OK' : '⚠ HOT (≥95°C) — check cooling'));
        out.textContent = L.join('\n');
        st.textContent = 'stress test done';
    } catch (e) { st.textContent = ''; out.textContent = 'error: ' + e.message; }
}

async function tuneApply() {
    const st = $('#tune-status');
    st.textContent = 'applying (you may be asked for your password)…';
    try {
        const r = await api.tune.apply();
        st.textContent = (r && r.ok) ? 'applied ✓ — some changes take effect after reboot' : ((r && r.error) || 'apply failed');
        tuneRefreshStatus();
    } catch (e) { st.textContent = 'error: ' + e.message; }
}

async function tuneReset() {
    const st = $('#tune-status');
    if (!window.confirm('Remove all Outlaw tuning and reset system settings to defaults?')) return;
    st.textContent = 'resetting…';
    try {
        const r = await api.tune.reset();
        st.textContent = (r && r.ok) ? 'reset ✓' : ((r && r.error) || 'reset failed');
        tuneRefreshStatus();
    } catch (e) { st.textContent = 'error: ' + e.message; }
}

async function tuneRefreshStatus() {
    const el = $('#tune-applied'); if (!el) return;
    try {
        const r = await api.tune.status();
        const d = r && r.data;
        if (d && d.applied !== false && d.governor) {
            el.textContent = 'currently applied: governor ' + d.governor + ', swappiness ' + d.swappiness +
                (d.zram_mb > 0 ? (', zram ' + d.zram_mb + ' MB') : '');
        } else {
            el.textContent = 'not tuned yet';
        }
    } catch { el.textContent = ''; }
}

// P1 — apply a visual theme by toggling a body class. The actual palette lives
// in styles.css (body.theme-gold { --term: …; }), so this is a zero-cost swap
// of CSS custom properties; nothing re-renders beyond a repaint.
function applyTheme(theme) {
    document.body.classList.toggle('theme-gold', theme === 'gold');
}

async function loadSettings() {
    let s = {};
    try { s = await api.settings.get(); } catch {}
    document.body.classList.toggle('crt', !!s.crtFx);
    document.body.classList.toggle('glow', !!s.glow);
    $('#crt-toggle').checked = !!s.crtFx;
    $('#glow-toggle').checked = !!s.glow;
    // P1 — theme. 'gold' adds body.theme-gold which re-points the CSS palette
    // variables to the gold-on-gunmetal scheme. Default 'green' = no class.
    applyTheme(s.theme || 'green');
    const themeSel = $('#theme-select');
    if (themeSel) themeSel.value = s.theme || 'green';
    // LM Studio handles model selection itself — no dropdown to seed.
    $('#perf-toggle').checked = !!s.performanceMode;
    $('#update-repo').value = s.updateRepo || '';
    const chanEl = $('#update-channel');
    if (chanEl) chanEl.value = s.updateChannel || 'stable';
    $('#auto-check').checked = !!s.autoCheck;
    $('#sponsor-url').value = s.sponsorUrl || '';
    // P2 — stability reporting: label the current version + reflect any
    // prior local vote. The community tally is fetched lazily (button /
    // first Settings open) so there's zero network cost otherwise.
    _stabilityReports = s.stabilityReports || {};
    refreshStabilityUi();
    // P5 — reflect any already-applied per-machine tuning.
    tuneRefreshStatus();
    // SC5 — System Core voice toggle. Probe TTS engine availability in
    // parallel with reading the setting so the sub-text reflects reality
    // (e.g., "On · piper" vs "On · not installed").
    const voiceEl = $('#voice-toggle');
    if (voiceEl) {
        voiceEl.checked = !!s.coreVoiceEnabled;
        refreshVoiceSubText(!!s.coreVoiceEnabled);
    }
    // SC7 — VRAM saver mode dropdown.
    const vramEl = $('#vram-mode');
    if (vramEl) {
        vramEl.value = s.vramSaverMode || 'auto';
        refreshVramSubText();
    }
}

async function refreshVramSubText() {
    const sub = $('#vram-mode-sub');
    if (!sub || !api.vram) return;
    try {
        const st = await api.vram.status();
        const lines = [];
        lines.push('Currently: ' + (st.label || st.tier));
        if (st.available && st.totalMb > 0) {
            lines.push(st.freeMb + ' / ' + st.totalMb + ' MB free');
        }
        sub.textContent = lines.join(' · ');
    } catch {
        sub.textContent = 'probe failed';
    }
}

async function refreshVoiceSubText(enabledHint) {
    const sub = $('#voice-sub');
    if (!sub || !api.tts) return;
    try {
        const st = await api.tts.status();
        const enabled = enabledHint != null ? !!enabledHint : !!st.enabled;
        if (!st.available) {
            sub.textContent = enabled
                ? 'On · ' + (st.note || 'no engine installed')
                : 'Off · ' + (st.note || 'no engine installed');
        } else if (enabled) {
            sub.textContent = 'On · speaking via ' + st.engine;
        } else {
            sub.textContent = 'Off · text-only bubble';
        }
    } catch {
        sub.textContent = 'Off · text-only bubble';
    }
}

async function setSetting(patch) { try { await api.settings.set(patch); } catch {} }

async function refreshGaming() {
    try {
        const g = await api.gaming.status();
        $('#gaming-status').textContent =
            `GPU ${g.gpu || 'unknown'}  •  GameMode ${g.gamemode ? 'available' : 'not installed'}  •  MangoHud ${g.mangohud ? 'available' : 'not installed'}`;
    } catch { $('#gaming-status').textContent = 'GPU info available on Outlaw OS.'; }
}

// ---------------------------------------------------------------------------
// Power / hotswap
// ---------------------------------------------------------------------------
function openPower() { $('#power-modal').classList.add('show'); }
function closePower() { $('#power-modal').classList.remove('show'); }
async function hotswap() {
    closePower();
    const r = await api.power.hotswap();
    toast(r.ok ? 'Opening hotswap…' : (r.error || 'Hotswap unavailable.'));
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------------------------------------------------------------------
// Event wiring (delegation)
// ---------------------------------------------------------------------------
function wire() {
    // Boot
    $('#boot-skip').addEventListener('click', enterOS);
    $('#boot-noai').addEventListener('click', async () => { await setSetting({ aiEnabled: false }); await refreshAiStatus(); enterOS(); toast('Started without AI.'); });

    // Sidebar nav
    $('#nav').addEventListener('click', (e) => {
        const item = e.target.closest('.nav-item[data-screen]');
        if (item) showScreen(item.dataset.screen);
    });

    // Global click delegation for data-action + data-launch
    document.body.addEventListener('click', async (e) => {
        const launch = e.target.closest('[data-launch]');
        if (launch) { launchApp(launch.dataset.launch); return; }
        const launchDisc = e.target.closest('[data-launch-disc]');
        if (launchDisc) { launchDiscoveredApp(launchDisc.dataset.launchDisc); return; }
        const installBtn = e.target.closest('[data-install-id]');
        if (installBtn) { handleAppsInstall(installBtn.dataset.installId); return; }
        const uninstallBtn = e.target.closest('[data-uninstall-id]');
        if (uninstallBtn) { handleAppsUninstall(uninstallBtn.dataset.uninstallId); return; }
        const filterChip = e.target.closest('[data-apps-filter]');
        if (filterChip) { setAppsFilter(filterChip.dataset.appsFilter); return; }
        if (e.target.id === 'apps-refresh-db') {
            toast('Refreshing package list… enter your password if prompted.');
            try {
                const r = await api.apps.refreshDb();
                toast(r.ok ? 'Package list refreshed.' : 'Refresh failed.');
                if (r.ok) refreshAppsInstalledOnly();
            } catch (err) {
                toast('Refresh failed: ' + err.message);
            }
            return;
        }
        const fileRow = e.target.closest('.fs-row');
        if (fileRow) {
            if (fileRow.dataset.type === 'dir') loadFiles((currentDir.endsWith('/') ? currentDir : currentDir + '/') + fileRow.dataset.name);
            else { const r = await api.files.open((currentDir.endsWith('/') ? currentDir : currentDir + '/') + fileRow.dataset.name); if (!r.ok) toast(r.error); }
            return;
        }
        const calcBtn = e.target.closest('#calc-pad [data-k]');
        if (calcBtn) { calcKey(calcBtn.dataset.k); return; }

        const act = e.target.closest('[data-action]');
        if (!act) return;
        switch (act.dataset.action) {
            case 'files-up': if (parentDir) loadFiles(parentDir); break;
            case 'files-home': loadFiles(await api.files.home()); break;
            case 'tasks-refresh': refreshTasks(); break;
            case 'ai-send': sendAI(); break;
            case 'updates-check': {
                $('#update-status').textContent = 'checking…';
                const r = await api.updates.check();
                $('#update-status').textContent = r.note || `${r.updates} update(s) available`;
                break;
            }
            case 'updates-apply': {
                $('#update-status').textContent = 'applying (enter password if prompted)…';
                const r = await api.updates.apply();
                $('#update-status').textContent = r.ok ? 'system updated' : (r.error || 'update failed');
                break;
            }
            case 'check-shell': checkShellUpdate(); break;
            case 'install-shell': installShellUpdate(); break;
            case 'rollback-shell': rollbackShell(); break;
            case 'installer': { const r = await api.installer.launch(); toast(r.ok ? 'Opening installer…' : r.error); break; }
            case 'hotswap': hotswap(); break;
            case 'power-menu': openPower(); break;
            case 'power-cancel': closePower(); break;
            case 'reboot': closePower(); api.power.reboot(); break;
            case 'shutdown': closePower(); api.power.shutdown(); break;
            case 'donate': {
                const url = ($('#sponsor-url').value || '').trim();
                if (!/^https?:\/\//i.test(url)) { toast('Add a sponsor URL first.'); break; }
                window.open(url, '_blank');
                break;
            }
            case 'stability-works':  setStabilityVote('works'); break;
            case 'stability-broken': setStabilityVote('broken'); break;
            case 'stability-refresh': refreshStabilityTally(); break;
            case 'stability-share':  shareStabilityFeedback(); break;
            case 'tune-scan':   tuneScan(); break;
            case 'tune-stress': tuneStress(); break;
            case 'tune-apply':  tuneApply(); break;
            case 'tune-reset':  tuneReset(); break;
            case 'confirm-cancel': closeConfirm(false); break;
        }
    });

    // Terminal
    const ti = $('#term-in');
    ti.addEventListener('input', () => inspectCommand(ti.value));
    ti.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { const c = ti.value.trim(); if (c) { ti.value = ''; $('#term-hint').textContent = ''; runTerminal(c); } }
    });

    // AI input
    $('#ai-in').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendAI(); });

    // Apps panel search — type-as-you-go, no debounce needed (catalog is tiny).
    const appsSearchEl = $('#apps-search');
    if (appsSearchEl) {
        appsSearchEl.addEventListener('input', (e) => setAppsSearch(e.target.value));
    }

    // Phase 2 — re-scan apps "On this PC" when the shell regains focus (e.g. after
    // downloading an AppImage in the browser and tabbing back), so it appears
    // without a manual refresh. Only does work while the Apps screen is open.
    window.addEventListener('focus', () => {
        if (!document.querySelector('#screen-apps.active')) return;
        api.apps.discover()
            .then((d) => { _appsState.discovered = d || []; if (_appsState.filter === 'discovered') _renderAppsList(); })
            .catch(() => {});
    });

    // Confirm modal
    $('#confirm-input').addEventListener('input', (e) => { $('#confirm-go').disabled = e.target.value.trim() !== 'CONFIRM'; });
    $('#confirm-go').addEventListener('click', () => closeConfirm(true));

    // Settings toggles
    $('#crt-toggle').addEventListener('change', (e) => { document.body.classList.toggle('crt', e.target.checked); setSetting({ crtFx: e.target.checked }); });
    $('#glow-toggle').addEventListener('change', (e) => { document.body.classList.toggle('glow', e.target.checked); setSetting({ glow: e.target.checked }); });
    const _themeSel = $('#theme-select');
    if (_themeSel) _themeSel.addEventListener('change', (e) => {
        const t = e.target.value === 'gold' ? 'gold' : 'green';
        applyTheme(t);
        setSetting({ theme: t });
        toast(t === 'gold' ? 'Gold Gunmetal engaged.' : 'Green Phosphor restored.');
    });
    $('#perf-toggle').addEventListener('change', async (e) => { await api.gaming.setPerformance(e.target.checked); toast('Performance mode ' + (e.target.checked ? 'ON' : 'OFF')); });
    // SC7 — Aggressive VRAM saver dropdown. setMode immediately invalidates
    // the probe cache + fires the tier-changed event, so the System Core
    // badge updates in the same beat the user picks a new mode.
    const vramModeEl = $('#vram-mode');
    if (vramModeEl) {
        vramModeEl.addEventListener('change', async (e) => {
            const mode = e.target.value;
            try {
                const r = await api.vram.setMode(mode);
                if (!r.ok) {
                    toast('VRAM mode change failed: ' + (r.error || 'unknown'));
                    return;
                }
                await refreshVramSubText();
                if (window.outlawCore && window.outlawCore.refreshVramTier) {
                    window.outlawCore.refreshVramTier();
                }
                toast('VRAM saver: ' + (r.status && r.status.label || mode));
            } catch (err) {
                toast('VRAM mode error: ' + err.message);
            }
        });
    }

    // SC5 — System Core voice toggle. Persist the setting, then re-probe TTS
    // status so the sub-text and the System Core footer both catch up without
    // requiring a screen revisit.
    const voiceToggleEl = $('#voice-toggle');
    if (voiceToggleEl) {
        voiceToggleEl.addEventListener('change', async (e) => {
            const on = !!e.target.checked;
            await setSetting({ coreVoiceEnabled: on });
            refreshVoiceSubText(on);
            // Force re-probe the engine in case the user just installed one
            // (caches were valid for up to 30s otherwise).
            try { await api.tts.status({ force: true }); } catch {}
            if (window.outlawCore && window.outlawCore.refreshVoiceStatus) {
                window.outlawCore.refreshVoiceStatus();
            }
            // Friendly nudge if they toggled on but no engine is installed.
            try {
                const st = await api.tts.status();
                if (on && !st.available) {
                    toast('Voice toggle on, but no TTS engine installed (piper / espeak-ng).');
                } else if (on) {
                    toast('Core voice on — speaking via ' + st.engine + '.');
                } else {
                    toast('Core voice off.');
                }
            } catch {}
        });
    }

    $('#ai-toggle').addEventListener('change', async (e) => {
        if (e.target.checked) {
            const r = await api.ai.enable();
            toast(r.available ? 'AI enabled.' : 'AI enabled — start LM Studio and click "Start Server".');
        } else {
            await api.ai.disable();
            toast('AI disabled.');
        }
        refreshAiStatus();
    });
    // Convenience: launch LM Studio from the AI settings card.
    const openLmBtn = $('#ai-open-lmstudio');
    if (openLmBtn) {
        openLmBtn.addEventListener('click', async () => {
            try {
                const r = await api.apps.launch('lmstudio');
                if (!r.ok) toast(r.error || 'Could not open LM Studio.');
            } catch {
                toast('Could not open LM Studio.');
            }
        });
    }

    // Session preference reset — flips ~/.outlaw-session-pref back to "ask"
    // so the greeter shows again on next boot.
    const sessResetBtn = $('#session-reset-pref');
    if (sessResetBtn) {
        sessResetBtn.addEventListener('click', async () => {
            try {
                const r = await api.session.resetGreeterPref();
                toast(r.ok ? 'Greeter will show on next boot.' : ('Reset failed: ' + (r.error || 'unknown')));
            } catch (err) {
                toast('Reset failed: ' + err.message);
            }
        });
    }

    // Session switcher: jump straight from the desktop into a Dev session.
    // Live-ISO welcome card buttons. The card itself is shown/hidden by
    // refreshLiveWelcome() on boot; these handlers cover the three actions
    // the user can take from it.
    const liveInstall = $('#live-install-btn');
    if (liveInstall) {
        liveInstall.addEventListener('click', async () => {
            try {
                const r = await api.installer.launch();
                if (!r || !r.ok) toast(r && r.error ? r.error : 'Could not launch installer.');
            } catch (e) {
                toast('Installer launch failed: ' + e.message);
            }
        });
    }
    const liveTry = $('#live-dismiss-btn');
    if (liveTry) {
        liveTry.addEventListener('click', () => {
            const card = $('#live-welcome');
            if (card) card.hidden = true;
            toast('Card hidden for this session — try anything you like, reboot to reset.');
        });
    }
    const liveNever = $('#live-never-btn');
    if (liveNever) {
        liveNever.addEventListener('click', async () => {
            const card = $('#live-welcome');
            if (card) card.hidden = true;
            try { await setSetting({ liveWelcomeDismissed: true }); } catch {}
            toast('Welcome card disabled permanently for this user.');
        });
    }

    const sessSwitchBtn = $('#session-switch-dev');
    if (sessSwitchBtn) {
        sessSwitchBtn.addEventListener('click', async () => {
            // Live demo: the Dev session needs the fully-installed system. Don't
            // offer to download a dev env into the ephemeral live overlay — just
            // tell the user to install first.
            if (_isLive) {
                window.alert(
                    "The Dev session isn't available in the live demo.\n\n" +
                    "Outlaw CodeMaker needs the full system. Install Outlaw OS to your " +
                    "disk first (click “Install Outlaw OS” on the desktop), then come " +
                    "back and switch to the Dev session.\n\n" +
                    "The live environment is a limited preview — install to unlock everything.",
                );
                return;
            }
            sessSwitchBtn.disabled = true;
            // First check the Dev session can actually run here. On a freshly
            // installed system where the dev env didn't build, CodeMaker's Python
            // deps may be missing — switching would just bounce back to the
            // desktop. Offer to download + build them instead of failing.
            let ready = true;
            try { const s = await api.session.devStatus(); ready = !!(s && s.ready); } catch { ready = true; }
            if (!ready) {
                const setup = window.confirm(
                    "The Dev session isn't set up on this machine yet.\n\n" +
                    "Outlaw CodeMaker needs Python and its dependencies (PyQt6, etc.). " +
                    "Download and build them now? This opens a terminal and needs an " +
                    "internet connection — a few minutes. When it finishes, click " +
                    "'Switch to Dev session' again.",
                );
                if (setup) {
                    try { await api.session.setupDev(); toast('Setting up the Dev session in a terminal — switch again once it finishes.'); }
                    catch (err) { toast('Could not start setup: ' + err.message); }
                }
                sessSwitchBtn.disabled = false;
                return;
            }
            const ok = window.confirm(
                'Switch to the Dev session now?\n\n' +
                'This closes the desktop and opens Outlaw CodeMaker. ' +
                'The screen will go black for a few seconds while X restarts.',
            );
            if (!ok) { sessSwitchBtn.disabled = false; return; }
            toast('Switching to Dev session…');
            try {
                const r = await api.session.switchToDev();
                if (!r.ok) {
                    toast('Could not switch: ' + (r.error || 'unknown error'));
                    sessSwitchBtn.disabled = false;
                }
                // On success the main process closes the window — nothing more to do.
            } catch (err) {
                toast('Switch failed: ' + err.message);
                sessSwitchBtn.disabled = false;
            }
        });
    }

    // Updater settings — persist on change.
    let repoSaveTimer = null;
    $('#update-repo').addEventListener('input', (e) => {
        clearTimeout(repoSaveTimer);
        repoSaveTimer = setTimeout(() => setSetting({ updateRepo: e.target.value.trim() }), 400);
    });
    $('#auto-check').addEventListener('change', (e) => {
        setSetting({ autoCheck: e.target.checked });
        toast('Auto-check ' + (e.target.checked ? 'enabled' : 'disabled') + '.');
    });
    const channelEl = $('#update-channel');
    if (channelEl) {
        channelEl.addEventListener('change', (e) => {
            const ch = e.target.value === 'beta' ? 'beta' : 'stable';
            setSetting({ updateChannel: ch });
            toast(ch === 'beta'
                ? 'Beta channel — you’ll get the newest (untested) builds.'
                : 'Stable channel — only tested releases.');
        });
    }
    let sponsorSaveTimer = null;
    $('#sponsor-url').addEventListener('input', (e) => {
        clearTimeout(sponsorSaveTimer);
        sponsorSaveTimer = setTimeout(() => setSetting({ sponsorUrl: e.target.value.trim() }), 400);
    });

    // Toast events from the main process (used by background update checks).
    api.on('toast', (msg) => toast(String(msg)));

    // Keyboard: physical calculator + escape closes modals
    document.addEventListener('keydown', (e) => {
        // Emergency stop: Ctrl+Alt+K → kill every tracked subprocess in main.
        // Works even when modal dialogs / hung handlers are blocking the rest
        // of the UI — last-resort escape hatch.
        if (e.key && e.key.toLowerCase() === 'k' && e.ctrlKey && e.altKey) {
            e.preventDefault();
            (async () => {
                try {
                    const r = await api.emergency.stop();
                    toast(`🛑 Emergency stop — killed ${r.killed} process(es).`);
                } catch (err) {
                    toast('Emergency stop failed: ' + err.message);
                }
            })();
            return;
        }
        if (e.key === 'Escape') { closePower(); if (confirmResolver) closeConfirm(false); }
        if ($('#screen-calc').classList.contains('active') && $('#app').classList.contains('ready')) {
            if (/[0-9.+\-*/]/.test(e.key)) calcKey(e.key);
            else if (e.key === 'Enter' || e.key === '=') calcKey('=');
            else if (e.key === 'Escape' || e.key.toLowerCase() === 'c') calcKey('C');
        }
    });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', async () => {
    wire();
    await loadSettings();
    await renderTiles();
    await loadSysInfo();
    // Probe whether a previous shell version exists on disk so the Rollback
    // button reflects reality on Settings open instead of waiting for a click.
    refreshRollbackAvailability().catch(() => {});
    // Live-ISO welcome card. Only shows when /run/archiso exists AND the user
    // hasn't ticked "Don't show again". Installed users never see this.
    refreshLiveWelcome().catch(() => {});
    calcRender();
    runBoot();
});

async function refreshLiveWelcome() {
    const card = $('#live-welcome');
    if (!api.system || !api.system.liveIso) return;
    try {
        const r = await api.system.liveIso();
        _isLive = !!(r && r.live);
        if (card) card.hidden = !(r && r.live && !r.dismissed);
        applyLiveLocks();
    } catch {
        if (card) card.hidden = true;
    }
}

// Live-mode ("broken mode", basic form): the live ISO is an ephemeral, limited
// preview — a teaser of the real thing. Lock the features that can't or
// shouldn't run until the OS is actually installed, and flag the limited state.
// The full glitch/fake-error aesthetic + a selectable "Broken" theme come later
// (roadmap). Installed systems never hit any of this.
let _isLive = false;
function applyLiveLocks() {
    if (!_isLive) return;
    document.body.classList.add('live-mode');
    const badge = $('#live-badge'); if (badge) badge.hidden = false;
    const reason = 'Locked in the live demo — install Outlaw OS to unlock this.';
    // Controls that don't make sense on a throwaway live system.
    ['#perf-toggle'].forEach((sel) => {
        const el = $(sel); if (el) { el.disabled = true; el.title = reason; }
    });
    const dev = $('#session-switch-dev');
    if (dev) dev.title = 'Install Outlaw OS first to use the Dev session.';
}
