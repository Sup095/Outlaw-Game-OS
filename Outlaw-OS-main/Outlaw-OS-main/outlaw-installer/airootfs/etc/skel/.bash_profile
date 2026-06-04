# Start the Outlaw OS graphical shell automatically on the first console.
# Booting to this console without launching X still works (Ctrl-Alt-F2 for a
# plain TTY), which keeps the system usable on very low-RAM machines.
if [[ -z "${DISPLAY:-}" && "${XDG_VTNR:-}" == "1" ]]; then
    exec startx
fi
