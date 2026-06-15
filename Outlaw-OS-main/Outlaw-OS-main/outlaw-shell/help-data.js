// ============================================================================
// Outlaw OS — Help database (Phase 6)
// ----------------------------------------------------------------------------
// A structured, shipped-in-the-image docs database. Each entry explains one
// part of the OS or one troubleshooting topic. The Help screen (renderer.js)
// renders these grouped by `cat` and searches title + keywords + body.
//
// Bodies are trusted, author-written HTML (simple tags only). Keep them short
// and practical. To add a topic: append an object; `cat` should match one of
// OUTLAW_HELP_CATS (new categories appear at the end).
// ============================================================================
window.OUTLAW_HELP_CATS = [
    'Getting started', 'The desktop', 'Apps & games', 'AI',
    'Dev session', 'System tools', 'Settings & updates', 'Troubleshooting',
];

window.OUTLAW_HELP = [
    // ---- Getting started ---------------------------------------------------
    {
        id: 'what-is', cat: 'Getting started', title: 'What is Outlaw OS?',
        keywords: 'overview about intro what outlaw os godot ai',
        body: '<p><b>Outlaw OS</b> is a lightweight, security-minded Linux desktop built for '
            + 'AI-driven game development with <b>Godot</b>. It has two sides: the <b>Desktop</b> '
            + '(this shell — apps, games, AI assistant, system tools) and the <b>Dev session</b> '
            + '(<b>Outlaw CodeMaker</b>, an AI agent that helps you build games). You pick which '
            + 'one to open at the boot greeter.</p>',
    },
    {
        id: 'first-steps', cat: 'Getting started', title: 'First things to do',
        keywords: 'setup begin start new fresh checklist wifi pin theme',
        body: '<ol><li>Connect to the internet — <b>Settings → Network &amp; Wi-Fi</b>.</li>'
            + '<li>Set a <b>PIN</b> if you want sign-in — <b>Settings → Security</b>.</li>'
            + '<li>Pick a look — <b>Settings → Appearance</b> (Green Phosphor or Gold Gunmetal).</li>'
            + '<li>Install your apps — <b>Apps</b> page (Steam, Firefox, Godot…).</li>'
            + '<li>Set up local AI — <b>AI Assistant → Set up your private AI → Check my PC</b>.</li></ol>',
    },
    {
        id: 'sessions', cat: 'Getting started', title: 'Desktop vs Dev session',
        keywords: 'greeter switch session dev desktop codemaker boot choose',
        body: '<p>At boot, the <b>greeter</b> lets you choose <b>Desktop</b> (this shell) or '
            + '<b>Dev</b> (Outlaw CodeMaker). You can switch later from <b>Settings → Session → '
            + 'Switch to Dev session</b>. Tick "always start in this session" to skip the greeter; '
            + 'reset that under <b>Settings → Session → Show greeter on next boot</b>.</p>',
    },

    // ---- The desktop -------------------------------------------------------
    {
        id: 'nav', cat: 'The desktop', title: 'Finding your way around',
        keywords: 'navigation sidebar screens menu layout dashboard',
        body: '<p>The left <b>sidebar</b> switches screens: Dashboard, System Core, Files, '
            + 'Task Manager, Terminal, Gaming, Game Dev, Apps, AI Assistant, Calculator and '
            + 'Settings. The top bar shows live CPU/RAM and the clock.</p>',
    },
    {
        id: 'windows', cat: 'The desktop', title: 'Windows & the taskbar',
        keywords: 'minimize maximize close taskbar tint2 openbox window manage drag',
        body: '<p>App windows have normal title-bar buttons — <b>minimize, maximize, close</b> — '
            + 'and a <b>taskbar</b> along the bottom shows what’s open (left-click to '
            + 'restore/hide, middle-click to close). This is handled by a tiny window manager '
            + '(openbox + tint2) that loads on top of the desktop.</p>',
    },
    {
        id: 'themes', cat: 'The desktop', title: 'Themes & the retro look',
        keywords: 'theme appearance green phosphor gold gunmetal crt scanline color',
        body: '<p><b>Settings → Appearance</b> switches between <b>Green Phosphor</b> (classic '
            + 'terminal green) and <b>Gold Gunmetal</b> (the sci-fi-fortress look that matches '
            + 'CodeMaker). There’s also an optional <b>CRT</b> effect. The whole UI recolors '
            + 'instantly — no restart.</p>',
    },

    // ---- Apps & games ------------------------------------------------------
    {
        id: 'apps', cat: 'Apps & games', title: 'Installing apps',
        keywords: 'apps install software packages essentials catalog pacman',
        body: '<p>The <b>Apps</b> page installs software for you. <b>Essentials</b> (Steam, '
            + 'Firefox, Godot) and security tools may ask for your password. Everything installs '
            + 'through the system package manager, so updates are handled centrally '
            + '(<b>Settings → System Package Updates</b>).</p>',
    },
    {
        id: 'on-this-pc', cat: 'Apps & games', title: 'Apps already on this PC',
        keywords: 'on this pc discover installed appimage desktop launch found',
        body: '<p>The Apps page has an <b>On this PC</b> view that auto-discovers everything '
            + 'you’ve installed — both regular <code>.desktop</code> apps and <b>AppImages</b> '
            + 'you download into Downloads/Applications. One click launches them; no manual '
            + 'refresh needed.</p>',
    },
    {
        id: 'appimages', cat: 'Apps & games', title: 'Running AppImages',
        keywords: 'appimage download run executable fuse lm studio chmod',
        body: '<p>An <b>AppImage</b> is a single-file app. Save it to <b>Downloads</b> or '
            + '<b>Applications</b> and it shows up under <b>Apps → On this PC</b>. Outlaw makes it '
            + 'executable and runs it for you (it includes FUSE support, with an automatic '
            + 'fallback if FUSE is missing). LM Studio is installed exactly this way.</p>',
    },

    // ---- AI ----------------------------------------------------------------
    {
        id: 'ai-setup', cat: 'AI', title: 'Setting up local AI (LM Studio)',
        keywords: 'ai lm studio model download setup check pc recommend server port 1234',
        body: '<p>Open <b>AI Assistant</b> and click <b>Check my PC</b> — Outlaw reads your RAM, '
            + 'GPU and CPU and recommends a model your hardware can run, plus a tiny starter model '
            + 'that runs on anything. Then: <b>Get / Open LM Studio</b> → download the Linux '
            + '<code>.AppImage</code> → click the button again to launch it → download the model → '
            + 'click <b>Start Server</b> (port 1234) → flip <b>Enable on-device AI</b> in Settings. '
            + 'Stuck? Use the <b>Ask the AI to set itself up</b> box.</p>',
    },
    {
        id: 'ai-assistant', cat: 'AI', title: 'Using the AI Assistant',
        keywords: 'assistant chat ask commands open app search private local',
        body: '<p>Everything runs <b>locally</b> — no account, nothing leaves your machine. Ask '
            + 'questions, or give commands like <i>open steam</i>, <i>search godot tutorials</i> or '
            + '<i>list files</i>. It already knows your hardware, so it can answer things like '
            + '“can my PC run a bigger model?”</p>',
    },
    {
        id: 'system-core', cat: 'AI', title: 'The System Core',
        keywords: 'system core telemetry diagnostics voice tts live ai sci-fi monitor',
        body: '<p>The <b>System Core</b> is the sci-fi centerpiece: live telemetry (CPU/RAM/GPU), '
            + 'three-tier <b>diagnostics</b>, and an optional <b>Live AI</b> with a dystopian '
            + 'persona that can run checks and open apps. It can speak aloud if you enable '
            + '<b>System Core voice</b> in Settings (needs a TTS engine).</p>',
    },

    // ---- Dev session -------------------------------------------------------
    {
        id: 'dev-session', cat: 'Dev session', title: 'The Dev session (Outlaw CodeMaker)',
        keywords: 'dev codemaker game development agent godot switch session pyqt',
        body: '<p><b>Outlaw CodeMaker</b> is the AI game-dev agent — a separate session focused '
            + 'on building Godot games (project picker, new-game wizard, AI that plans and edits). '
            + 'Open it from the <b>greeter</b> or <b>Settings → Session → Switch to Dev session</b>. '
            + 'The first switch may download its tools; let it finish.</p>',
    },

    // ---- System tools ------------------------------------------------------
    {
        id: 'task-manager', cat: 'System tools', title: 'Task Manager',
        keywords: 'task manager end task process tree kill cpu ram gpu vram filter',
        body: '<p><b>Task Manager</b> lists everything running. Type in <b>Filter by name</b> to '
            + 'find an app, click its row, then <b>End task</b> (close nicely) or <b>End process '
            + 'tree</b> (force-close it and what it started). The cards up top show live '
            + '<b>CPU, memory and GPU/VRAM</b>. Some processes belong to the administrator and '
            + 'show <i>needs admin</i> when ended — that’s normal.</p>',
    },
    {
        id: 'terminal', cat: 'System tools', title: 'The Secure Terminal',
        keywords: 'terminal command shell sudo pacman destructive confirm rm',
        body: '<p>A normal shell running in your home folder as you. <b>Destructive commands</b> '
            + '(<code>rm -rf</code>, <code>dd</code>, <code>mkfs</code>…) are intercepted and '
            + 'need you to type <b>CONFIRM</b> first, so you can’t wipe the machine by '
            + 'accident. Use <code>sudo</code> for admin tasks.</p>',
    },
    {
        id: 'files', cat: 'System tools', title: 'Files',
        keywords: 'files browse folders open file manager thunar documents downloads',
        body: '<p>Browse your folders (Downloads, Documents, Pictures, Projects…). If a file '
            + 'won’t open from here, use <b>Open in file manager</b> to launch the full file '
            + 'manager (Thunar), which has the right “open with” handlers.</p>',
    },

    // ---- Settings & updates ------------------------------------------------
    {
        id: 'updates', cat: 'Settings & updates', title: 'Updating Outlaw OS',
        keywords: 'update updater shell channel stable beta repair package upgrade version',
        body: '<p><b>Settings → Outlaw Shell Updates</b> updates Outlaw itself (shell + all its '
            + 'components) with one click, and keeps the previous version for rollback. '
            + '<b>System Package Updates</b> updates every installed app + the system. '
            + 'Note: before v1.0 there’s <b>no difference</b> between the Stable and Beta '
            + 'channels — both get the newest build. <b>Reinstall / repair all components</b> fixes '
            + 'a half-applied update without touching your files.</p>',
    },
    {
        id: 'security', cat: 'Settings & updates', title: 'PIN & sign-in',
        keywords: 'pin sign in lock password security authorize unlock',
        body: '<p><b>Settings → Security</b> lets you set a <b>4-digit PIN</b> and require sign-in '
            + 'on startup. The PIN (or your password) is also asked before important installs like '
            + 'Essentials and security tools. Forgot the PIN? Use <b>Use password instead</b> at '
            + 'the sign-in screen.</p>',
    },

    // ---- Troubleshooting ---------------------------------------------------
    {
        id: 'trouble-boot', cat: 'Troubleshooting', title: 'It won’t boot / black screen',
        keywords: 'boot black screen wont start graphics kms virtualbox vm bare metal',
        body: '<p>Outlaw is built and tested for <b>bare-metal</b> machines — that’s the '
            + 'supported path. Virtual machines (especially VirtualBox on a Windows host with '
            + 'Hyper-V/WSL2 enabled) are flaky and <b>not officially supported</b>. On real '
            + 'hardware, a black screen after the menu usually means a graphics-mode hiccup — try '
            + 'rebooting once. If it persists, note your GPU and report it.</p>',
    },
    {
        id: 'trouble-gfx', cat: 'Troubleshooting', title: 'Desktop crashes or black screen (safe graphics)',
        keywords: 'crash black screen safe mode graphics gpu intel software gl mesa picom watchdog',
        body: '<p>If the desktop crashes on startup, Outlaw automatically retries in <b>safe '
            + 'graphics</b> — software rendering with GPU acceleration turned off — which runs on '
            + 'practically any hardware (handy for flaky Intel/Mesa GPUs that report '
            + '“failed to load module intel”). It then <b>stays</b> in safe graphics so it can’t '
            + 'crash-loop; everything still works, it’s just not GPU-accelerated. To retry your '
            + 'GPU later, open a <b>Terminal</b> and run <code>rm ~/.outlaw-safe-gfx</code>, then '
            + 'reboot.</p>',
    },
    {
        id: 'trouble-wifi', cat: 'Troubleshooting', title: 'No internet / Wi-Fi',
        keywords: 'wifi internet network offline connect nmcli download fails',
        body: '<p>Go to <b>Settings → Network &amp; Wi-Fi</b>, scan, pick your network and enter '
            + 'the password. A wired cable works automatically. Most “install failed” or '
            + '“can’t download” errors are simply no connection — check here first.</p>',
    },
    {
        id: 'trouble-ai', cat: 'Troubleshooting', title: 'LM Studio won’t connect',
        keywords: 'lm studio not reachable ai disabled start server port 1234 model',
        body: '<p>If the AI says LM Studio isn’t reachable: open LM Studio, <b>load a '
            + 'model</b>, then click <b>Start Server</b> (it must be on <b>port 1234</b>). Then '
            + 'turn on <b>Enable on-device AI</b> in Settings. If the button won’t open LM '
            + 'Studio, make sure the <code>.AppImage</code> is in your Downloads/Applications '
            + 'folder and press <b>Get / Open LM Studio</b> again.</p>',
    },
    {
        id: 'trouble-install', cat: 'Troubleshooting', title: 'Install problems',
        keywords: 'install installer partition disk erase free space windows shrink wifi',
        body: '<p>Connect to the internet <b>before</b> installing (the installer needs to '
            + 'download packages). Choose <b>erase a disk</b> or <b>use free space</b>. '
            + '<i>Known issue:</i> automatic Windows-partition shrinking can fail right now — if it '
            + 'does, make free space first (e.g. shrink the partition from Windows), then pick '
            + '<b>use free space</b>.</p>',
    },
];
