#!/bin/sh
# ============================================================================
# Outlaw OS — live session launcher (USB-boot ISO).
# ----------------------------------------------------------------------------
# The live image autologins as root. It goes STRAIGHT TO THE DESKTOP — no
# greeter — because (a) the desktop is where the "Install Outlaw OS" welcome
# card lives, and (b) it's the most reliable path on a fresh VM. You can still
# switch to the Dev session from the shell's Settings (it writes
# /root/.outlaw-session and relaunches).
#
# No first-boot wizard here: the live environment is ephemeral (tmpfs), so
# installing optional bundles into RAM would be wasteful. The wizard runs on
# the INSTALLED system's first boot instead.
#
# All stderr goes to /root/.outlaw-x.log so silent Electron failures (the
# black-screen-on-VBox symptom) are diagnosable from a TTY.
# ============================================================================

XLOG="/root/.outlaw-x.log"
exec 2>>"$XLOG"
printf '\n=== outlaw live .xinitrc start: %s ===\n' "$(date -Iseconds)" >&2

# Inside a VM, force software OpenGL (kills the black-screen-on-VBox failure).
if command -v systemd-detect-virt >/dev/null 2>&1; then
    if [ "$(systemd-detect-virt 2>/dev/null)" != "none" ]; then
        export LIBGL_ALWAYS_SOFTWARE=1
        export GALLIUM_DRIVER=llvmpipe
        echo "[xinitrc] VM detected — forcing software GL." >&2
    fi
fi

picom --config /root/.config/picom/picom.conf >/dev/null 2>&1 &

ELECTRON_FLAGS="$(outlaw-electron-flags 2>/dev/null | tr '\n' ' ')"
[ -z "$ELECTRON_FLAGS" ] && ELECTRON_FLAGS="--no-sandbox"

# Default to Desktop. Honor a saved choice if the user switched sessions from
# the shell (the switcher writes /root/.outlaw-session, then relaunches X).
SESSION="$(head -n1 /root/.outlaw-session 2>/dev/null | tr -d '[:space:]')"
[ -z "$SESSION" ] && SESSION="desktop"

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
    SESSION="$(head -n1 /root/.outlaw-session 2>/dev/null | tr -d '[:space:]')"
    [ -z "$SESSION" ] && SESSION="desktop"
    run_session "$SESSION"
fi
