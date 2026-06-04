# Live env root often uses zsh — start the shell from .zprofile too.
if [[ -z "${DISPLAY:-}" && "${XDG_VTNR:-}" == "1" ]]; then
    exec startx
fi
