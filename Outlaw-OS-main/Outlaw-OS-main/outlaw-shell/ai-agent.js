// ============================================================================
// Outlaw OS - Local AI agent (LM Studio backend)
// ----------------------------------------------------------------------------
// A deliberately lightweight wrapper around a *local* LM Studio instance
// (http://127.0.0.1:1234/v1, OpenAI-compatible). No data ever leaves the
// machine. LM Studio handles model loading and VRAM management itself, so this
// module never pulls weights — the user picks a model in LM Studio and clicks
// "Start Server".
//
// This module ONLY does model I/O + parsing. It never executes anything; the
// main process decides which proposed tool calls are safe to run and which
// require explicit user confirmation.
// ============================================================================

const LM_STUDIO_BASE = 'http://127.0.0.1:1234/v1';
// Sent to LM Studio as the "model" field. It auto-selects the currently loaded
// model when given this sentinel — matches Outlaw CodeMaker's default.
const DEFAULT_MODEL = 'local-model';

// Tools the model is allowed to propose. Keep the surface tiny so small models
// stay reliable. `run_command` is intentionally marked dangerous so the main
// process always routes it through user confirmation.
const TOOLS = ['answer', 'open_app', 'search_web', 'list_files', 'open_file', 'system_info', 'install_app', 'run_command'];

function systemPrompt(appIds, machine) {
    return [
        'You are Cr1tt3r, the on-device assistant for Outlaw OS — a Linux for AI-driven Godot game development.',
        'Persona: a sharp, friendly cyber-outlaw sidekick — concise, practical, a little wry, never showy. If asked your name, you are Cr1tt3r.',
        'You are privacy-respecting: everything runs locally on this machine, so no data ever leaves it.',
        ...(machine ? ['', 'This computer: ' + machine] : []),
        '',
        'Reply with EXACTLY ONE line of minified JSON and nothing else. Schema:',
        '{"tool":"<tool>","arg":"<string>","text":"<short message to the user>"}',
        '',
        'Tools:',
        '- answer: just talk to the user (arg empty). Use for questions, explanations, chat.',
        '- open_app: launch an installed app. arg = one of: ' + appIds.join(', ') + '.',
        '- search_web: open the browser on a web search. arg = the search query.',
        '- list_files: show a directory. arg = absolute path (default home if empty).',
        '- open_file: open a file/folder with its default app. arg = path.',
        '- system_info: report CPU/RAM/host info (arg empty).',
        '- install_app: install software the user names OR describes. arg = the most likely PACKAGE name (e.g. "something to edit audio" -> "audacity"; "a photo editor" -> "gimp"). Any official package works, not just famous ones — give your single best package-name guess. Known sources only (Apps catalog / official repos); the user confirms before anything installs.',
        '- run_command: ONLY when the user explicitly asks to run a shell command. arg = the command. This always asks the user to confirm first.',
        '',
        'Rules: pick the single best tool. Prefer "answer" for anything conversational.',
        'Never invent file paths. Keep "text" under 240 characters. Output JSON only.',
    ].join('\n');
}

async function timeoutFetch(url, opts, ms) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), ms);
    try {
        return await fetch(url, { ...opts, signal: ctrl.signal });
    } finally {
        clearTimeout(t);
    }
}

// Is LM Studio reachable, and which models are loaded?
// LM Studio exposes /v1/models à la OpenAI; it returns the currently-loaded model(s).
async function status(opts = {}) {
    const base = opts.baseUrl || LM_STUDIO_BASE;
    try {
        const res = await timeoutFetch(`${base}/models`, {}, 1500);
        if (!res.ok) return { available: false, models: [] };
        const data = await res.json();
        // OpenAI shape: { data: [{ id, object, ... }, ...] }
        const models = Array.isArray(data && data.data) ? data.data.map((m) => m.id).filter(Boolean) : [];
        return { available: true, models };
    } catch {
        return { available: false, models: [] };
    }
}

// LM Studio doesn't have a "pull this model" API — the user picks a model in
// LM Studio's UI and clicks Start Server. We keep this function as a no-op so
// callers that used to call ensureModel() still work; it just verifies the
// server is up.
async function ensureModel(_model, onProgress) {
    const s = await status();
    if (!s.available) {
        throw new Error(
            'LM Studio is not reachable. Open LM Studio, load a model, then click "Start Server" (port 1234).',
        );
    }
    if (onProgress) onProgress('LM Studio is serving a model.');
    return true;
}

function parseIntent(raw) {
    // Be lenient: small models sometimes wrap JSON in prose or code fences.
    if (!raw) return { tool: 'answer', arg: '', text: 'No response.' };
    let text = raw.trim();
    const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
    if (fence) text = fence[1].trim();
    const start = text.indexOf('{');
    const end = text.lastIndexOf('}');
    if (start !== -1 && end > start) {
        try {
            const obj = JSON.parse(text.slice(start, end + 1));
            const tool = TOOLS.includes(obj.tool) ? obj.tool : 'answer';
            return {
                tool,
                arg: typeof obj.arg === 'string' ? obj.arg : '',
                text: typeof obj.text === 'string' ? obj.text : '',
            };
        } catch {
            /* fall through to plain answer */
        }
    }
    return { tool: 'answer', arg: '', text: raw.trim() };
}

// Ask the model. Returns a parsed intent { tool, arg, text }.
// Uses the OpenAI-compatible /v1/chat/completions endpoint.
async function ask(prompt, { model, appIds, machine, baseUrl, history, summary } = {}) {
    const messages = [{ role: 'system', content: systemPrompt(appIds || [], machine) }];
    // Phase 15b — persistent-chat memory: an optional summary of older turns plus
    // the recent window, so Cr1tt3r can follow context across a conversation
    // (e.g. "open it" after naming an app). Caller supplies both; both optional.
    if (summary) {
        messages.push({ role: 'system', content: 'Summary of earlier conversation: ' + String(summary).slice(0, 1500) });
    }
    if (Array.isArray(history)) {
        for (const m of history) {
            if (m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content) {
                messages.push({ role: m.role, content: m.content.slice(0, 2000) });
            }
        }
    }
    messages.push({ role: 'user', content: String(prompt || '').slice(0, 4000) });
    const body = {
        model: model || DEFAULT_MODEL,
        stream: false,
        temperature: 0.2,
        // Keep responses short — we only need a single JSON line. This also caps
        // VRAM cost on small machines.
        max_tokens: 256,
        messages,
    };
    const res = await timeoutFetch(`${baseUrl || LM_STUDIO_BASE}/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }, 1000 * 120);
    if (!res.ok) {
        const detail = await res.text().catch(() => '');
        throw new Error(`AI request failed (HTTP ${res.status}). ${detail.slice(0, 200)}`);
    }
    const data = await res.json();
    // OpenAI shape: { choices: [{ message: { role, content } }, ...] }
    const content =
        data && data.choices && data.choices[0] && data.choices[0].message
            ? data.choices[0].message.content
            : '';
    return parseIntent(content);
}

// Plain conversational completion — NO JSON-intent contract. Used by the
// hardware-aware AI setup guide so even a tiny local model can answer in normal
// prose (the {tool,arg,text} schema is too tight for a step-by-step walkthrough).
// `messages` is a full OpenAI-style array (system + turns). Returns raw text.
async function chat(messages, { model, maxTokens = 400, baseUrl } = {}) {
    const body = {
        model: model || DEFAULT_MODEL,
        stream: false,
        temperature: 0.4,
        max_tokens: maxTokens,
        messages: Array.isArray(messages) ? messages : [],
    };
    const res = await timeoutFetch(`${baseUrl || LM_STUDIO_BASE}/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }, 1000 * 120);
    if (!res.ok) {
        const detail = await res.text().catch(() => '');
        throw new Error(`AI request failed (HTTP ${res.status}). ${detail.slice(0, 200)}`);
    }
    const data = await res.json();
    const content =
        data && data.choices && data.choices[0] && data.choices[0].message
            ? data.choices[0].message.content
            : '';
    return String(content || '').trim();
}

module.exports = { status, ensureModel, ask, chat, TOOLS };
