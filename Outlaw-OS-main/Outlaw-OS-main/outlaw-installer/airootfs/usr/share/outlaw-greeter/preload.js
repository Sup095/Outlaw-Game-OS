// ============================================================================
// Outlaw OS — Session greeter (preload bridge)
// ----------------------------------------------------------------------------
// One method. The renderer can only ask the main process to record a session
// choice — it can never write to disk directly.
// ============================================================================

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('greeter', {
    // remember=true tells main to mirror the choice into the persistent pref
    // file (`~/.outlaw-session-pref`) so future boots skip the greeter entirely.
    choose: (choice, remember) => ipcRenderer.invoke('greeter:choose', choice, !!remember),
});
