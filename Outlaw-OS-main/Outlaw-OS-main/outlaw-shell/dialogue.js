// ============================================================================
// Outlaw OS — System Core dialogue picker (SC5)
// ----------------------------------------------------------------------------
// Loads dialogue.json once at module init (fetch, since the renderer has no
// fs access). After load, callers use the SYNCHRONOUS pickSync(event, params)
// — that lets the syscore.js state machine handle dialogue without converting
// every transition to async.
//
// Design rules:
//   * No immediate repeats per event. If you only have 1 line for an event,
//     you get it every time; with 2+, the same index never fires twice in a
//     row.
//   * {placeholder}-style interpolation, simple regex. Missing placeholders
//     keep their literal "{key}" so bugs are visible instead of silently
//     dropped.
//   * pickSync returns null until the JSON has finished loading. Callers
//     should fall back to a hardcoded string in that window — typically
//     it's a millisecond or two on startup.
//   * Zero ongoing work. Module does one fetch at boot, then idles.
// ============================================================================
'use strict';

(function () {
    let _data = null;          // catalogue, null until fetch resolves
    let _ready = null;         // load promise (for callers who want await)
    let _failed = false;
    const _lastIdx = Object.create(null);

    function _load() {
        if (_ready) return _ready;
        // Relative URL works in Electron's file:// context and matches the
        // shell's CSP (connect-src 'self').
        _ready = fetch('dialogue.json')
            .then((r) => {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then((d) => { _data = d; return d; })
            .catch((err) => {
                console.warn('[dialogue] catalogue load failed:', err);
                _failed = true;
                _data = null;
                return null;
            });
        return _ready;
    }

    function _interpolate(s, params) {
        if (!params) return s;
        return String(s).replace(/\{(\w+)\}/g, (m, k) =>
            params[k] != null ? String(params[k]) : m);
    }

    /**
     * Pick a line for `event`, interpolating any {placeholders} from params.
     * Returns null when the catalogue hasn't loaded yet OR when the event
     * isn't in the catalogue. Avoids repeating the same index twice in a row.
     */
    function pickSync(event, params) {
        if (!_data) return null;
        const lines = _data[event];
        if (!Array.isArray(lines) || lines.length === 0) return null;
        let idx = Math.floor(Math.random() * lines.length);
        if (lines.length > 1 && idx === _lastIdx[event]) {
            idx = (idx + 1) % lines.length;
        }
        _lastIdx[event] = idx;
        return _interpolate(lines[idx], params);
    }

    /** True once dialogue.json finished loading (successfully or otherwise). */
    function isReady() {
        return _data !== null || _failed;
    }

    /** Promise resolving when the catalogue is loaded. Useful for boot. */
    function whenReady() {
        return _load();
    }

    /** Enumerate the event keys we have lines for. Debug-only. */
    function eventKeys() {
        return _data ? Object.keys(_data).filter((k) => !k.startsWith('_')) : [];
    }

    // Kick off the fetch immediately — the catalogue is tiny (<5 KB) and we
    // want it ready by the time the user opens the System Core screen.
    _load();

    window.outlawDialogue = {
        pickSync,
        isReady,
        whenReady,
        eventKeys,
    };
})();
