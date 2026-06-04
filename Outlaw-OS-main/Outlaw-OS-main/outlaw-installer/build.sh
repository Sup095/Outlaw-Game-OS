#!/usr/bin/env bash
# ============================================================================
# Outlaw OS - Boot Manager / Live ISO build script
# ----------------------------------------------------------------------------
# Strategy: take the known-good upstream `releng` archiso profile as a base
# (so bootloader scaffolding is always correct for the installed archiso), then
# overlay this repo's customizations on top. This avoids hand-maintaining
# fragile BIOS/UEFI boot configs.
#
# Run on Arch Linux with: sudo ./build.sh
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
OUT_DIR="$REPO_ROOT/out"
WORK_DIR="$OUT_DIR/work"
PROFILE_DIR="$OUT_DIR/build-profile"
RELENG="/usr/share/archiso/configs/releng"
# Default version baked in for local builds. CI overrides this from the git
# tag via OUTLAW_ISO_VERSION (see .github/workflows/build-iso.yml) so the
# artifact filename always matches the tag the user pushed.
ISO_VERSION="${OUTLAW_ISO_VERSION:-2.0.2}"
ISO_FINAL="$OUT_DIR/outlaw-os-v${ISO_VERSION}.iso"

echo "========================================"
echo "   Building Outlaw OS Live ISO v${ISO_VERSION}"
echo "========================================"

# --- Preconditions ---------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (mkarchiso needs it). Try: sudo ./build.sh"
    exit 1
fi
if ! command -v mkarchiso >/dev/null 2>&1; then
    echo "ERROR: archiso is not installed. Run: pacman -S archiso"
    exit 1
fi
if [[ ! -d "$RELENG" ]]; then
    echo "ERROR: $RELENG not found (is the 'archiso' package installed?)."
    exit 1
fi

# --- Clean -----------------------------------------------------------------
echo "[1/7] Cleaning previous build…"
rm -rf "$WORK_DIR" "$PROFILE_DIR" "$ISO_FINAL" "$ISO_FINAL.sha256" 2>/dev/null || true
mkdir -p "$OUT_DIR"

# --- Assemble profile from releng base + our overlays ----------------------
echo "[2/7] Assembling profile from releng base…"
cp -r "$RELENG" "$PROFILE_DIR"

# Overlay our package set and pacman.conf
cp "$HERE/packages.x86_64" "$PROFILE_DIR/packages.x86_64"
cp "$HERE/pacman.conf"     "$PROFILE_DIR/pacman.conf"

# Merge our airootfs overlay (scripts, skel, root profile, hostname)
cp -rT "$HERE/airootfs" "$PROFILE_DIR/airootfs"

# Sync the Electron shell into the image (single source of truth: outlaw-shell)
echo "[3/7] Syncing Outlaw shell…"
install -d "$PROFILE_DIR/airootfs/usr/share/outlaw-os"
cp -rT "$REPO_ROOT/outlaw-shell" "$PROFILE_DIR/airootfs/usr/share/outlaw-os"
rm -rf "$PROFILE_DIR/airootfs/usr/share/outlaw-os/node_modules" 2>/dev/null || true

# Bundle Outlaw CodeMaker into /opt/outlaw-codemaker. The path can be
# overridden with OUTLAW_CODEMAKER_SRC for non-standard checkouts. We try a
# few sensible defaults so a sibling clone works out of the box. If nothing
# is found, the bundle step skips with a warning and the greeter's Dev path
# falls back to the desktop shell — the ISO still builds.
echo "[4/7] Bundling Outlaw CodeMaker…"
CM_DEFAULTS=(
    "${OUTLAW_CODEMAKER_SRC:-}"
    "$REPO_ROOT/../Outlaw CodeMaker"
    "$REPO_ROOT/../outlaw-codemaker"
    "$REPO_ROOT/outlaw-codemaker"
)
CM_SRC=""
for path in "${CM_DEFAULTS[@]}"; do
    if [[ -n "$path" && -f "$path/main.py" ]]; then
        CM_SRC="$path"
        break
    fi
done
if [[ -n "$CM_SRC" ]]; then
    "$HERE/bundle-codemaker.sh" "$CM_SRC" "$PROFILE_DIR/airootfs"
else
    echo "  ⚠ CodeMaker source not found in any default location."
    echo "    Set OUTLAW_CODEMAKER_SRC=/abs/path/to/codemaker and re-run to include it."
    echo "    Continuing without CodeMaker — the greeter's Dev path will fall back to desktop."
fi

# Enable services + live autologin-to-shell (creating symlinks on Linux side)
echo "[5/7] Enabling services and autologin…"
ROOTFS="$PROFILE_DIR/airootfs"
install -d "$ROOTFS/etc/systemd/system/multi-user.target.wants"
ln -sf /usr/lib/systemd/system/NetworkManager.service \
       "$ROOTFS/etc/systemd/system/multi-user.target.wants/NetworkManager.service"
ln -sf /usr/lib/systemd/system/apparmor.service \
       "$ROOTFS/etc/systemd/system/multi-user.target.wants/apparmor.service"
# Autologin root on tty1. Name this drop-in so it sorts AFTER any releng one
# (`override.conf` comes after `autologin.conf`), so our ExecStart wins.
install -d "$ROOTFS/etc/systemd/system/getty@tty1.service.d"
cat > "$ROOTFS/etc/systemd/system/getty@tty1.service.d/override.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=-/usr/bin/agetty --autologin root --noclear %I 38400 linux
EOF

# Patch profiledef.sh (keep releng bootmodes; override naming + permissions)
echo "[6/7] Patching profile metadata…"
PD="$PROFILE_DIR/profiledef.sh"
sed -i \
    -e 's/^iso_name=.*/iso_name="outlaw-os"/' \
    -e "s/^iso_version=.*/iso_version=\"${ISO_VERSION}\"/" \
    -e 's/^iso_publisher=.*/iso_publisher="Outlaw OS"/' \
    -e 's#^iso_application=.*#iso_application="Outlaw OS Live / Boot Manager"#' \
    "$PD"
# Ensure our helper scripts are executable in the image
for f in outlaw-install outlaw-hotswap outlaw-perf outlaw-update-apply \
         outlaw-update-rollback outlaw-greeter outlaw-codemaker outlaw-lm-studio \
         outlaw-session-watchdog outlaw-diagnose; do
    if ! grep -q "/usr/local/bin/$f" "$PD"; then
        sed -i "/^file_permissions=(/a\\  [\"/usr/local/bin/$f\"]=\"0:0:755\"" "$PD"
    fi
done

# --- Build -----------------------------------------------------------------
echo "[7/7] Running mkarchiso (this takes a while)…"
mkarchiso -v -w "$WORK_DIR" -o "$OUT_DIR" "$PROFILE_DIR"

# mkarchiso outputs outlaw-os-<version>-x86_64.iso — normalize the name.
BUILT_ISO="$(find "$OUT_DIR" -maxdepth 1 -name 'outlaw-os-*.iso' -type f | head -n1 || true)"
if [[ -z "$BUILT_ISO" ]]; then
    echo "❌ ERROR: ISO not produced. Check the mkarchiso output above."
    exit 1
fi
mv -f "$BUILT_ISO" "$ISO_FINAL"
sha256sum "$ISO_FINAL" | sed "s#$OUT_DIR/##" > "$ISO_FINAL.sha256"

echo "✅ Build complete:"
echo "   ISO: $ISO_FINAL"
echo "   SHA: $ISO_FINAL.sha256"
ls -lh "$ISO_FINAL"*
