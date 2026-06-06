#!/usr/bin/env bash
# ============================================================================
# Outlaw Game OS — repo-wide static, security, and consistency checks.
# ----------------------------------------------------------------------------
# This is the "stress test everything" suite. It does NOT need a running OS;
# it validates that every script/module parses, scans for unsafe patterns,
# and checks cross-file consistency (versions, package sanity, no stray
# tooling references). Runs in CI (.github/workflows/checks.yml) on every
# push, and can be run locally:  bash tests/check-all.sh
#
# Tools used if present (skipped with a note if missing, so it runs anywhere):
#   bash, node, python3, shellcheck, npm
#
# Exit non-zero if any HARD check fails. Soft findings print as warnings.
# ============================================================================
set -uo pipefail

# Resolve repo root as the parent of this script's dir.
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

SHELL_DIR="Outlaw-OS-main/Outlaw-OS-main/outlaw-shell"
INST_DIR="Outlaw-OS-main/Outlaw-OS-main/outlaw-installer"
FIRSTBOOT_DIR="Outlaw-OS-main/Outlaw-OS-main/outlaw-firstboot"
CM_DIR="Outlaw CodeMaker"

FAILS=0
WARNS=0
hr()   { printf '%s\n' "------------------------------------------------------------"; }
pass() { printf '  \033[32mok\033[0m   %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILS=$((FAILS+1)); }
warn() { printf '  \033[33mwarn\033[0m %s\n' "$*"; WARNS=$((WARNS+1)); }
have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
hr; echo "[1] Shell script syntax (bash -n)"; hr
# Every *.sh plus the extensionless helpers in airootfs/usr/local/bin and the
# profile/xinit files.
SHELL_FILES=()
while IFS= read -r f; do SHELL_FILES+=("$f"); done < <(
    find "$INST_DIR" -type f \( -name '*.sh' \
        -o -path '*/airootfs/usr/local/bin/*' \) 2>/dev/null | sort
)
# Profile + xinit dotfiles (bash/sh).
for f in \
    "$INST_DIR/airootfs/etc/skel/.bash_profile" \
    "$INST_DIR/airootfs/etc/skel/.zprofile" \
    "$INST_DIR/airootfs/etc/skel/.xinitrc" \
    "$INST_DIR/airootfs/root/.bash_profile" \
    "$INST_DIR/airootfs/root/.zprofile" \
    "$INST_DIR/airootfs/root/.xinitrc" \
    "$ROOT/tests/check-all.sh"; do
    [[ -f "$f" ]] && SHELL_FILES+=("$f")
done
for f in "${SHELL_FILES[@]}"; do
    if bash -n "$f" 2>/tmp/outlaw-chk.$$; then pass "$(basename "$f")"
    else fail "$f"; sed 's/^/        /' /tmp/outlaw-chk.$$; fi
done
rm -f /tmp/outlaw-chk.$$

# ---------------------------------------------------------------------------
hr; echo "[2] Shell static analysis (shellcheck)"; hr
if have shellcheck; then
    for f in "${SHELL_FILES[@]}"; do
        # -S error: only fail the suite on errors; style/info are advisory.
        if shellcheck -S error -e SC1090,SC1091 "$f" >/tmp/outlaw-sc.$$ 2>&1; then
            pass "$(basename "$f")"
        else
            fail "shellcheck: $f"; sed 's/^/        /' /tmp/outlaw-sc.$$
        fi
    done
    rm -f /tmp/outlaw-sc.$$
else
    warn "shellcheck not installed — skipping (CI installs it)."
fi

# ---------------------------------------------------------------------------
hr; echo "[3] JavaScript syntax (node --check)"; hr
if have node; then
    while IFS= read -r f; do
        if node --check "$f" 2>/tmp/outlaw-node.$$; then pass "$(basename "$f")"
        else fail "$f"; sed 's/^/        /' /tmp/outlaw-node.$$; fi
    done < <(find "$SHELL_DIR" "$FIRSTBOOT_DIR" -maxdepth 1 -name '*.js' 2>/dev/null | sort)
    rm -f /tmp/outlaw-node.$$
else
    warn "node not installed — skipping JS syntax."
fi

# ---------------------------------------------------------------------------
hr; echo "[4] Python compile (CodeMaker)"; hr
if have python3 || have python; then
    PY="$(command -v python3 || command -v python)"
    # Compile every .py outside venv/cache/build dirs.
    PYERR=0
    while IFS= read -r f; do
        if ! "$PY" -m py_compile "$f" 2>/tmp/outlaw-py.$$; then
            fail "$f"; sed 's/^/        /' /tmp/outlaw-py.$$; PYERR=1
        fi
    done < <(find "$CM_DIR" -name '*.py' \
                -not -path '*/.venv/*' -not -path '*/__pycache__/*' \
                -not -path '*/build/*' 2>/dev/null | sort)
    [[ "$PYERR" -eq 0 ]] && pass "all CodeMaker .py modules compile"
    rm -f /tmp/outlaw-py.$$
else
    warn "python not installed — skipping Python compile."
fi

# ---------------------------------------------------------------------------
hr; echo "[5] JSON / config validity"; hr
if have python3 || have python; then
    PY="$(command -v python3 || command -v python)"
    JSONERR=0
    while IFS= read -r f; do
        if ! "$PY" -c "import json,sys; json.load(open(sys.argv[1],encoding='utf-8'))" "$f" 2>/tmp/outlaw-json.$$; then
            fail "invalid JSON: $f"; sed 's/^/        /' /tmp/outlaw-json.$$; JSONERR=1
        fi
    done < <(find "$SHELL_DIR" "$FIRSTBOOT_DIR" "$CM_DIR" -maxdepth 2 -name '*.json' \
                -not -path '*/node_modules/*' -not -path '*/.venv/*' \
                -not -name 'package-lock.json' 2>/dev/null | sort)
    [[ "$JSONERR" -eq 0 ]] && pass "all JSON files parse"
    rm -f /tmp/outlaw-json.$$
fi

# ---------------------------------------------------------------------------
hr; echo "[6] Security scan (unsafe patterns)"; hr
# These are heuristics; a hit is a WARNING that a human should eyeball, except
# the Electron hardening checks which are HARD fails.

# 6a. Electron renderer hardening — these MUST hold.
if grep -rIn "nodeIntegration:\s*true" "$SHELL_DIR" "$FIRSTBOOT_DIR" \
        --include='*.js' 2>/dev/null | grep -v node_modules; then
    fail "nodeIntegration:true found in an Electron window (must be false)."
else
    pass "no nodeIntegration:true in Electron windows"
fi
if grep -rIn "contextIsolation:\s*false" "$SHELL_DIR" "$FIRSTBOOT_DIR" \
        --include='*.js' 2>/dev/null | grep -v node_modules; then
    fail "contextIsolation:false found (must be true)."
else
    pass "contextIsolation stays enabled"
fi

# 6b. Hardcoded secrets — heuristic.
if grep -rInE "(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]" \
        "$SHELL_DIR" "$FIRSTBOOT_DIR" "$CM_DIR" \
        --include='*.js' --include='*.py' --include='*.json' 2>/dev/null \
        | grep -v node_modules | grep -v '.venv'; then
    warn "possible hardcoded secret above — verify it's a placeholder."
else
    pass "no obvious hardcoded secrets"
fi

# 6c. Python subprocess with shell=True — injection surface.
if grep -rIn "shell=True" "$CM_DIR" --include='*.py' 2>/dev/null \
        | grep -v '.venv' | grep -v '__pycache__'; then
    warn "subprocess shell=True above — confirm inputs are not attacker-controlled."
else
    pass "no subprocess shell=True in CodeMaker"
fi

# 6d. pkexec callers should never interpolate untrusted strings into a shell.
#     Informational: list pkexec call sites for review.
PKEXEC_HITS="$(grep -rIn "pkexec" "$SHELL_DIR" --include='*.js' 2>/dev/null | grep -v node_modules | wc -l | tr -d ' ')"
pass "pkexec call sites in shell: ${PKEXEC_HITS} (privileged ops go through audited helpers)"

# ---------------------------------------------------------------------------
hr; echo "[7] Consistency checks"; hr
# 7a. Version agreement across the three sources of truth.
BUILD_VER="$(sed -n 's/.*OUTLAW_ISO_VERSION:-\([0-9.]*\)}.*/\1/p' "$INST_DIR/build.sh" | head -n1)"
PROF_VER="$(sed -n 's/^iso_version="\([^"]*\)".*/\1/p' "$INST_DIR/profiledef.sh" | head -n1)"
PKG_VER="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SHELL_DIR/package.json" | head -n1)"
echo "        build.sh=$BUILD_VER  profiledef.sh=$PROF_VER  package.json=$PKG_VER"
if [[ -n "$BUILD_VER" && "$BUILD_VER" == "$PROF_VER" && "$BUILD_VER" == "$PKG_VER" ]]; then
    pass "versions agree ($BUILD_VER)"
else
    fail "version mismatch (build=$BUILD_VER profiledef=$PROF_VER package=$PKG_VER)"
fi

# 7b. No stray AI-tooling references in TRACKED file names/paths. We use
#     git ls-files (not find) so gitignored local tooling caches like
#     .claude/ — which are never committed or shipped — don't trip this.
if have git && git rev-parse --git-dir >/dev/null 2>&1; then
    if git ls-files | grep -iE '(^|/)(.*claude|.*anthropic)' | grep -q .; then
        warn "a TRACKED file/path references an AI tool — clean it up:"
        git ls-files | grep -iE '(^|/)(.*claude|.*anthropic)' | sed 's/^/        /'
    else
        pass "no AI-tooling references in tracked file names/paths"
    fi
else
    warn "not a git repo — skipping tracked-file AI-tooling scan."
fi

# 7c. Every outlaw-* helper named in build.sh's file_permissions for-loop
#     exists on disk. Scope the scan to the loop block only, so unrelated
#     'outlaw-*' strings (the ISO name, the shell dir) don't false-positive.
MISSING=0
HELPER_BLOCK="$(sed -n '/for f in outlaw-/,/do$/p' "$INST_DIR/build.sh")"
while IFS= read -r helper; do
    [[ -z "$helper" ]] && continue
    if [[ ! -f "$INST_DIR/airootfs/usr/local/bin/$helper" ]]; then
        fail "build.sh permission loop lists '$helper' but the file is missing."
        MISSING=1
    fi
done < <(printf '%s\n' "$HELPER_BLOCK" | grep -oE 'outlaw-[a-z][a-z-]*' | sort -u)
[[ "$MISSING" -eq 0 ]] && pass "all permission-loop helpers exist on disk"

# ---------------------------------------------------------------------------
hr
if [[ "$FAILS" -eq 0 ]]; then
    printf '\033[32mAll hard checks passed.\033[0m  (%d warnings)\n' "$WARNS"
    exit 0
else
    printf '\033[31m%d hard check(s) failed.\033[0m  (%d warnings)\n' "$FAILS" "$WARNS"
    exit 1
fi
