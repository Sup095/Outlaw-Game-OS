# Outlaw OS live environment: start the graphical shell on the first console.
# Uses outlaw-start-session so a failed X start drops to this root shell with
# a readable error instead of an invisible getty→startx crash loop.
if [[ -z "${DISPLAY:-}" && "${XDG_VTNR:-}" == "1" ]]; then
    if command -v outlaw-start-session >/dev/null 2>&1; then
        outlaw-start-session
    else
        exec startx
    fi
fi
