#!/usr/bin/env bash
# ninja-publisher — Phase C2.2: sudo apt install Playwright Chromium deps
#
# Requires sudo. Idempotent. Writes only under /var/cache/apt + /var/lib/dpkg
# + /usr/lib (via apt-get install). Never touches anything under $HOME.
#
# Never reads from, writes to, or cd's into:
#   - $HOME/ninja-butler-brain/  (Butler's live brain)
#   - $HOME/.openclaw/           (Butler's active workspace)
#   - $HOME/n8n-claw/            (n8n-claw container stack dir — unrelated)
#   - /etc (except apt's normal cache state)
#
# Meant to be `bash scripts/butler/c2-2-syspkg.sh` — boss runs it by hand;
# apt will prompt for her sudo password once.
#
# Package list matches Playwright's Ubuntu 24.04 requirements. The `t64`
# suffix on libasound2t64 / libgtk-3-0t64 / libevent-2.1-7t64 is mandatory
# on Noble (64-bit time_t transition); the un-suffixed names 404 in 24.04.

set -euo pipefail

# ---------------------------------------------------------------------------
# Guard 1: Butler's brain must exist and must not change during this run.
# ---------------------------------------------------------------------------
BUTLER_BRAIN="$HOME/ninja-butler-brain"
if [[ ! -d "$BUTLER_BRAIN" ]]; then
  echo "❌ CRITICAL: ~/ninja-butler-brain/ missing. ABORT. Do not proceed." >&2
  exit 1
fi
BUTLER_BRAIN_MTIME_BEFORE="$(stat -c %Y "$BUTLER_BRAIN" 2>/dev/null || echo "unknown")"

# ---------------------------------------------------------------------------
# Guard 2: refuse to run as root directly. Boss runs as normal user and
# sudo is invoked internally — that keeps $HOME = /home/uniceadmin (so
# the brain guard resolves correctly) and limits root's blast radius to
# apt-get commands only.
# ---------------------------------------------------------------------------
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "❌ Run as normal user, not root (sudo is invoked internally)." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PKGS=(
  libnss3
  libatk-bridge2.0-0t64   # t64 on Noble (libatk-bridge2.0-0 is a transitional dummy)
  libatk1.0-0t64          # t64 on Noble
  libxkbcommon0
  libasound2t64
  libgbm1
  libcups2t64             # t64 on Noble
  libpango-1.0-0
  libgtk-3-0t64
  libwoff1
  libharfbuzz-icu0
  libgstreamer-plugins-base1.0-0
  libvpx9
  libevent-2.1-7t64
)

log()  { printf '[c2-2] %s\n' "$*"; }
die()  { printf '[c2-2] ❌ %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Step 1: idempotent survey — which packages are already installed (ii)?
# ---------------------------------------------------------------------------
MISSING=()
for pkg in "${PKGS[@]}"; do
  if ! dpkg-query -W -f='${Status}\n' "$pkg" 2>/dev/null | grep -q '^install ok installed$'; then
    MISSING+=("$pkg")
  fi
done

log "Requested: ${#PKGS[@]} packages"
log "Already installed: $(( ${#PKGS[@]} - ${#MISSING[@]} ))"
log "Missing (will install): ${#MISSING[@]}"

# ---------------------------------------------------------------------------
# Step 2: install any missing packages (sudo, may prompt for password)
# ---------------------------------------------------------------------------
if [[ ${#MISSING[@]} -eq 0 ]]; then
  log "Nothing to install. Skipping apt-get update."
else
  log "Missing: ${MISSING[*]}"
  log "Running: sudo apt-get update"
  sudo apt-get update
  log "Running: sudo apt-get install -y ${MISSING[*]}"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${MISSING[@]}"
fi

# ---------------------------------------------------------------------------
# Verification — every package must be ii, brain must be untouched.
# ---------------------------------------------------------------------------
log ""
log "===== Verification ====="

ALL_OK=true
INSTALLED_COUNT=0
for pkg in "${PKGS[@]}"; do
  if dpkg-query -W -f='${Status}\n' "$pkg" 2>/dev/null | grep -q '^install ok installed$'; then
    log "✅ $pkg"
    INSTALLED_COUNT=$(( INSTALLED_COUNT + 1 ))
  else
    log "❌ $pkg NOT INSTALLED"
    ALL_OK=false
  fi
done

[[ -d "$BUTLER_BRAIN" ]] || die "BUTLER_BRAIN disappeared — halt, investigate"
BUTLER_BRAIN_MTIME_AFTER="$(stat -c %Y "$BUTLER_BRAIN" 2>/dev/null || echo "unknown")"
if [[ "$BUTLER_BRAIN_MTIME_BEFORE" != "$BUTLER_BRAIN_MTIME_AFTER" ]]; then
  die "BUTLER_BRAIN mtime changed ($BUTLER_BRAIN_MTIME_BEFORE → $BUTLER_BRAIN_MTIME_AFTER). Halt — something touched it."
fi
log "✅ ~/ninja-butler-brain untouched (mtime $BUTLER_BRAIN_MTIME_BEFORE)"

log ""
if $ALL_OK; then
  log "✅ All ${#PKGS[@]} Playwright Chromium deps installed, 0 ❌"
  log ""
  log "Phase C2.2 syspkg complete. Ready for C2.3 (uv pip install + playwright install chromium)."
else
  die "$(( ${#PKGS[@]} - INSTALLED_COUNT )) of ${#PKGS[@]} packages missing — see ❌ lines above"
fi
