#!/bin/sh
# ============================================================================
# Outlaw OS — live session launcher (USB-boot ISO).
# Same routing as the installed-system .xinitrc; lives under /root because the
# live image autologins as root.
# ============================================================================

picom --config /root/.config/picom/picom.conf >/dev/null 2>&1 &

if [ "${OUTLAW_SKIP_GREETER:-0}" != "1" ]; then
    outlaw-greeter 2>/dev/null || true
fi

SESSION="$(head -n1 /root/.outlaw-session 2>/dev/null | tr -d '[:space:]')"
[ -z "$SESSION" ] && SESSION="desktop"

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
    SESSION="$(head -n1 /root/.outlaw-session 2>/dev/null | tr -d '[:space:]')"
    [ -z "$SESSION" ] && SESSION="desktop"
    run_session "$SESSION"
fi
