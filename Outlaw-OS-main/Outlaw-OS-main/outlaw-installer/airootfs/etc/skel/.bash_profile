# Start the Outlaw OS graphical shell automatically on the first console.
# Booting to this console without launching X still works (Ctrl-Alt-F2 for a
# plain TTY), which keeps the system usable on very low-RAM machines.
#
# We call outlaw-start-session (NOT `exec startx`) so that if the graphical
# session crash-loops, the wrapper drops us back to THIS interactive shell
# with a readable error instead of an invisible getty→startx→crash loop.
if [[ -z "${DISPLAY:-}" && "${XDG_VTNR:-}" == "1" ]]; then
    if command -v outlaw-start-session >/dev/null 2>&1; then
        outlaw-start-session
    else
        exec startx   # fallback for older images without the wrapper
    fi
fi
