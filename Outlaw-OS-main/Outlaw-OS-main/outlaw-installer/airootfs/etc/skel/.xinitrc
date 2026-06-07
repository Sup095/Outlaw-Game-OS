#!/bin/sh
# ============================================================================
# Outlaw OS — desktop session launcher (installed system).
# ----------------------------------------------------------------------------
# Boot flow:
#   FIRST boot (no ~/.outlaw-firstboot-done marker yet):
#     1. Force the DESKTOP session — the greeter is skipped entirely. This is
#        the most reliable path: it gets you to a known-good desktop where the
#        first-boot wizard installs the optional bundles (Steam / Firefox /
#        Godot) and you can confirm everything works.
#     2. The wizard writes ~/.outlaw-firstboot-done when you finish or skip.
#   LATER boots (marker present):
#     3. Run the greeter so you can pick Dev or Desktop each boot.
#
#   The chosen session is then launched under the crash-guard watchdog:
#     dev      → outlaw-codemaker (PyQt AI game-dev agent)
#     desktop  → electron-wrapped /usr/share/outlaw-os (standard shell)
#     tty      → exit X, dropping back to the login console
#
# All stderr goes to ~/.outlaw-x.log so silent Electron failures are
# diagnosable. Fail-safe: anything missing/unreadable falls back to Desktop.
# ============================================================================

XLOG="$HOME/.outlaw-x.log"
exec 2>>"$XLOG"
printf '\n=== outlaw .xinitrc start: %s ===\n' "$(date -Iseconds)" >&2

# Detect whether we're in a VM. Used for two things below: forcing software
# GL, and SKIPPING the compositor (picom).
OUTLAW_IN_VM=0
if command -v systemd-detect-virt >/dev/null 2>&1; then
    if [ "$(systemd-detect-virt 2>/dev/null)" != "none" ]; then
        OUTLAW_IN_VM=1
        export LIBGL_ALWAYS_SOFTWARE=1
        export GALLIUM_DRIVER=llvmpipe
        echo "[xinitrc] VM detected — forcing software GL, skipping picom." >&2
    fi
fi

# picom is purely cosmetic (shadows / vsync) and its glx backend needs real
# OpenGL. In a VM the software framebuffer has no usable GLX, and a
# half-working glx compositor REDIRECTS every window and then paints it BLACK
# — the Electron window appears but stays dark. So only run picom on real
# hardware, where KMS gives working GL. The desktop renders fine without it.
if [ "$OUTLAW_IN_VM" = "0" ]; then
    picom --config "$HOME/.config/picom/picom.conf" >/dev/null 2>&1 &
fi

# Electron flag set (adds --disable-gpu inside a VM). Used for greeter + shell.
ELECTRON_FLAGS="$(outlaw-electron-flags 2>/dev/null | tr '\n' ' ')"
[ -z "$ELECTRON_FLAGS" ] && ELECTRON_FLAGS="--no-sandbox"

# ---- Decide the session --------------------------------------------------
if [ ! -f "$HOME/.outlaw-firstboot-done" ]; then
    # FIRST BOOT: install deps + go straight to Desktop. No greeter.
    echo "[xinitrc] first boot — running setup wizard, forcing Desktop." >&2
    if command -v outlaw-firstboot >/dev/null 2>&1; then
        outlaw-firstboot || echo "[xinitrc] first-boot wizard exited non-zero (continuing)." >&2
    fi
    SESSION="desktop"
else
    # LATER BOOTS: let the greeter pick (unless a kiosk override is set).
    if [ "${OUTLAW_SKIP_GREETER:-0}" != "1" ]; then
        outlaw-greeter || true
    fi
    SESSION="$(head -n1 "$HOME/.outlaw-session" 2>/dev/null | tr -d '[:space:]')"
    [ -z "$SESSION" ] && SESSION="desktop"
fi

# ---- Launch the session under the crash-guard watchdog -------------------
run_session() {
    case "$1" in
        dev)
            outlaw-session-watchdog dev outlaw-codemaker
            ;;
        desktop)
            # shellcheck disable=SC2086  # intentional word-splitting of flags
            outlaw-session-watchdog desktop electron $ELECTRON_FLAGS /usr/share/outlaw-os
            ;;
        tty|*)
            return 0
            ;;
    esac
}

run_session "$SESSION"
RC=$?
if [ "$RC" = 87 ]; then
    # Watchdog forced a fallback. Re-read the session file and try once more.
    SESSION="$(head -n1 "$HOME/.outlaw-session" 2>/dev/null | tr -d '[:space:]')"
    [ -z "$SESSION" ] && SESSION="desktop"
    run_session "$SESSION"
fi
