#!/bin/sh
# ============================================================================
# Outlaw OS — desktop session launcher (installed system).
# ----------------------------------------------------------------------------
# Boot flow:
#   1. picom starts in the background (clean vsync compositor, no tearing).
#   2. outlaw-greeter runs (Electron, kiosk-ish window) and writes the user's
#      session choice to ~/.outlaw-session.
#   3. This script reads the choice and execs the matching session:
#        dev      → outlaw-codemaker (PyQt AI game-dev agent)
#        desktop  → electron /usr/share/outlaw-os (standard shell)
#        tty      → exit X, dropping back to the login console
#
# Fail-safe: if anything is missing or the choice can't be read, we fall back
# to the desktop shell so the user is never stranded.
# ============================================================================

picom --config "$HOME/.config/picom/picom.conf" >/dev/null 2>&1 &

# Run the greeter (blocks until the user picks). Honor a one-shot override:
# set OUTLAW_SKIP_GREETER=1 (e.g. in /etc/environment) to bypass it for kiosks.
if [ "${OUTLAW_SKIP_GREETER:-0}" != "1" ]; then
    outlaw-greeter 2>/dev/null || true
fi

SESSION="$(head -n1 "$HOME/.outlaw-session" 2>/dev/null | tr -d '[:space:]')"
[ -z "$SESSION" ] && SESSION="desktop"

# Wrap the actual session launch in outlaw-session-watchdog so 3 consecutive
# quick deaths flip us into safe-mode (Desktop) instead of looping forever.
# The watchdog re-writes ~/.outlaw-session if it forces a fallback, so we
# re-read it on a watchdog-triggered exit (code 87).
run_session() {
    case "$1" in
        dev)     outlaw-session-watchdog dev outlaw-codemaker ;;
        desktop) outlaw-session-watchdog desktop electron /usr/share/outlaw-os ;;
        tty|*)   return 0 ;;
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
