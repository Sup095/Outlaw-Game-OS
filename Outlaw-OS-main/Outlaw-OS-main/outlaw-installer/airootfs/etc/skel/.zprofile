# Mirror of .bash_profile for users whose shell is zsh.
if [[ -z "${DISPLAY:-}" && "${XDG_VTNR:-}" == "1" ]]; then
    exec startx
fi
