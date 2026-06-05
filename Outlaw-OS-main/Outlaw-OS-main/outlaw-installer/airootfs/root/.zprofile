# Live env root often uses zsh — start the shell from .zprofile too.
# Uses outlaw-start-session (crash-loop guard) instead of a blind exec startx.
if [[ -z "${DISPLAY:-}" && "${XDG_VTNR:-}" == "1" ]]; then
    if command -v outlaw-start-session >/dev/null 2>&1; then
        outlaw-start-session
    else
        exec startx
    fi
fi
