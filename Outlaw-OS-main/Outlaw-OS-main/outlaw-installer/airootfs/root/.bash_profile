# Outlaw OS live environment: start the graphical shell on the first console.
if [[ -z "${DISPLAY:-}" && "${XDG_VTNR:-}" == "1" ]]; then
    exec startx
fi
