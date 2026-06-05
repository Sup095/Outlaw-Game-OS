#!/bin/sh
# ============================================================================
# Outlaw OS — live session launcher (USB-boot ISO).
# Same routing as the installed-system .xinitrc; lives under /root because the
# live image autologins as root.
#
# Differences from /etc/skel/.xinitrc on installed systems:
#   * No first-boot wizard. Live ISO doesn't persist anything; a wizard that
#     would offer to install packages is pointless there.
#   * Hardcoded /root paths instead of $HOME (autologin user is root here).
#
# All stderr from greeter / shell goes to /root/.outlaw-x.log so silent
# Electron failures (the classic black-screen-with-cursor symptom on VBox
# without 3D acceleration) are diagnosable from a TTY after the fact.
# ============================================================================

XLOG="/root/.outlaw-x.log"
exec 2>>"$XLOG"

printf '\n=== outlaw live .xinitrc start: %s ===\n' "$(date -Iseconds)" >&2

picom --config /root/.config/picom/picom.conf >/dev/null 2>&1 &

if [ "${OUTLAW_SKIP_GREETER:-0}" != "1" ]; then
    outlaw-greeter || true
fi

SESSION="$(head -n1 /root/.outlaw-session 2>/dev/null | tr -d '[:space:]')"
[ -z "$SESSION" ] && SESSION="desktop"

# Build the Electron flag set so live ISO doesn't black-screen on VBox /
# QEMU. outlaw-electron-flags emits --no-sandbox always plus
# --disable-gpu --disable-gpu-compositing when inside a VM.
ELECTRON_FLAGS="$(outlaw-electron-flags 2>/dev/null | tr '\n' ' ')"
[ -z "$ELECTRON_FLAGS" ] && ELECTRON_FLAGS="--no-sandbox"

run_session() {
    case "$1" in
        dev)
            outlaw-session-watchdog dev outlaw-codemaker
            ;;
        desktop)
            # shellcheck disable=SC2086  # intentional word-splitting of ELECTRON_FLAGS
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
    SESSION="$(head -n1 /root/.outlaw-session 2>/dev/null | tr -d '[:space:]')"
    [ -z "$SESSION" ] && SESSION="desktop"
    run_session "$SESSION"
fi
