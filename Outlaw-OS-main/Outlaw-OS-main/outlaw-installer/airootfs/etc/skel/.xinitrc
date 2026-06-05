#!/bin/sh
# ============================================================================
# Outlaw OS — desktop session launcher (installed system).
# ----------------------------------------------------------------------------
# Boot flow:
#   1. picom starts in the background (clean vsync compositor, no tearing).
#   2. outlaw-firstboot runs ONCE on first login (checkbox UI: install Steam
#      / Firefox / Godot bundles). After the user picks, it touches
#      ~/.outlaw-firstboot-done so it never runs again. Skippable.
#   3. outlaw-greeter runs (Electron, kiosk-ish window) and writes the user's
#      session choice to ~/.outlaw-session.
#   4. This script reads the choice and execs the matching session:
#        dev      → outlaw-codemaker (PyQt AI game-dev agent)
#        desktop  → electron-wrapped /usr/share/outlaw-os (standard shell)
#        tty      → exit X, dropping back to the login console
#
# All stderr from the greeter / first-boot wizard / shell goes to
# ~/.outlaw-x.log so silent Electron failures (the classic black-screen
# symptom on VBox without 3D) are diagnosable after the fact.
#
# Fail-safe: if anything is missing or the choice can't be read, we fall back
# to the desktop shell so the user is never stranded.
# ============================================================================

XLOG="$HOME/.outlaw-x.log"
exec 2>>"$XLOG"

# Header so each X session is easy to find in the log.
printf '\n=== outlaw .xinitrc start: %s ===\n' "$(date -Iseconds)" >&2

# Inside a VM, force software OpenGL so Electron / picom never try to use a
# GPU path the virtual adapter can't provide. Combined with the
# --disable-gpu flags from outlaw-electron-flags, this kills the
# black-screen-on-VirtualBox failure for good. No-op on real hardware.
if command -v systemd-detect-virt >/dev/null 2>&1; then
    if [ "$(systemd-detect-virt 2>/dev/null)" != "none" ]; then
        export LIBGL_ALWAYS_SOFTWARE=1
        export GALLIUM_DRIVER=llvmpipe
        echo "[xinitrc] VM detected — forcing software GL (LIBGL_ALWAYS_SOFTWARE=1)." >&2
    fi
fi

# picom is nice-to-have, not essential. In a VM with no compositing support
# it can fail; that's fine, the shell still renders. Run it best-effort.
picom --config "$HOME/.config/picom/picom.conf" >/dev/null 2>&1 &

# First-boot wizard: install optional bundles (Steam stack, Firefox, Godot)
# the first time the user logs in. Marker file prevents re-runs.
if [ ! -f "$HOME/.outlaw-firstboot-done" ] && command -v outlaw-firstboot >/dev/null 2>&1; then
    echo "[xinitrc] first-boot wizard launching..." >&2
    outlaw-firstboot || echo "[xinitrc] first-boot wizard exited non-zero (ignoring)" >&2
fi

# Run the greeter (blocks until the user picks). Honor a one-shot override:
# set OUTLAW_SKIP_GREETER=1 (e.g. in /etc/environment) to bypass it for kiosks.
if [ "${OUTLAW_SKIP_GREETER:-0}" != "1" ]; then
    outlaw-greeter || true
fi

SESSION="$(head -n1 "$HOME/.outlaw-session" 2>/dev/null | tr -d '[:space:]')"
[ -z "$SESSION" ] && SESSION="desktop"

# Build the Electron flag set once so the desktop session launch matches the
# greeter (VM detection → --disable-gpu where needed).
ELECTRON_FLAGS="$(outlaw-electron-flags 2>/dev/null | tr '\n' ' ')"
[ -z "$ELECTRON_FLAGS" ] && ELECTRON_FLAGS="--no-sandbox"

# Wrap the actual session launch in outlaw-session-watchdog so 3 consecutive
# quick deaths flip us into safe-mode (Desktop) instead of looping forever.
# The watchdog re-writes ~/.outlaw-session if it forces a fallback, so we
# re-read it on a watchdog-triggered exit (code 87).
run_session() {
    case "$1" in
        dev)
            outlaw-session-watchdog dev outlaw-codemaker
            ;;
        desktop)
            # shellcheck disable=SC2086  # word-splitting of ELECTRON_FLAGS is intentional
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
