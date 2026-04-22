#!/usr/bin/env bash
# ninja-publisher — Phase C2.1 bootstrap (Butler VPS, no sudo)
#
# Idempotent. Writes only under:
#   - $HOME/ninja-clan/ (project target dir)
#   - $HOME/.local/    (uv binary via user install)
#   - $HOME/.bashrc    (appends PATH export once)
#
# Never reads from, writes to, or cd's into:
#   - $HOME/ninja-butler-brain/  (Butler's live brain)
#   - $HOME/.openclaw/           (Butler's active workspace)
#   - $HOME/n8n-claw/            (n8n-claw docker stack dir)
#   - /var, /etc, /usr, any system path
#
# Meant to be `bash scripts/butler/c2-1-bootstrap.sh` — boss runs it by
# hand in her ninja-clan SSH session after reviewing docs/c2-1-review.md.

set -euo pipefail

# ---------------------------------------------------------------------------
# Guard 1: Butler's brain must exist and must not change during this run.
# If either check fails we abort — never try to "fix" Butler.
# ---------------------------------------------------------------------------
BUTLER_BRAIN="$HOME/ninja-butler-brain"
if [[ ! -d "$BUTLER_BRAIN" ]]; then
  echo "❌ CRITICAL: ~/ninja-butler-brain/ missing. ABORT. Do not proceed." >&2
  exit 1
fi
BUTLER_BRAIN_MTIME_BEFORE="$(stat -c %Y "$BUTLER_BRAIN" 2>/dev/null || echo "unknown")"

# ---------------------------------------------------------------------------
# Guard 2: refuse to run as root. Everything goes into $HOME; root would
# point $HOME at /root and corrupt the whole layout.
# ---------------------------------------------------------------------------
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "❌ This script must run as a normal user (not root)." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NINJA_ROOT="$HOME/ninja-clan"
PUBLISHER_DIR="$NINJA_ROOT/butler/ninja-publisher"
POSTIZ_DIR="$NINJA_ROOT/postiz-app"
LOG_DIR="$PUBLISHER_DIR/logs"
LOCAL_BIN="$HOME/.local/bin"
BASHRC="$HOME/.bashrc"
MARKER="# ninja-publisher/c2-1-bootstrap: PATH for ~/.local/bin"

log()  { printf '[c2-1] %s\n' "$*"; }
warn() { printf '[c2-1] ⚠️  %s\n' "$*" >&2; }
die()  { printf '[c2-1] ❌ %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Step 1: scaffold ~/ninja-clan/{butler/ninja-publisher,postiz-app,.../logs}
# mkdir -p is idempotent; existing dirs are left untouched.
# ---------------------------------------------------------------------------
log "Scaffolding $NINJA_ROOT …"
mkdir -p "$PUBLISHER_DIR" "$POSTIZ_DIR" "$LOG_DIR"

# Secrets hygiene: pre-create an empty .env with 0600 perms so the first
# real write (Phase C2.4) can't accidentally land as 0644. Skip if a
# .env already exists — don't blow away whatever's there.
ENV_PATH="$PUBLISHER_DIR/.env"
if [[ ! -e "$ENV_PATH" ]]; then
  log "Creating empty $ENV_PATH (perms 0600)"
  ( umask 077 && : > "$ENV_PATH" )
else
  log "$ENV_PATH already exists — leaving it alone"
fi

# ---------------------------------------------------------------------------
# Step 2: ensure ~/.local/bin exists and is on PATH permanently (.bashrc)
# We guard the append with a marker line so re-running doesn't duplicate it.
# ---------------------------------------------------------------------------
mkdir -p "$LOCAL_BIN"

touch "$BASHRC"  # ensure file exists before grep

if grep -Fq "$MARKER" "$BASHRC"; then
  log "~/.bashrc already has the c2-1 PATH marker — skipping append"
else
  log "Appending ~/.local/bin to PATH in $BASHRC (first run)"
  {
    printf '\n%s\n' "$MARKER"
    printf '%s\n' 'export PATH="$HOME/.local/bin:$PATH"'
  } >> "$BASHRC"
fi

# Make uv visible to this script regardless of whether .bashrc was sourced
export PATH="$LOCAL_BIN:$PATH"

# ---------------------------------------------------------------------------
# Step 3: install uv (user install, no sudo) — pinned install dir so we
# don't depend on the installer's default location changing over releases.
# ---------------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  log "uv already present: $(uv --version 2>&1) ($(command -v uv))"
else
  log "Installing uv → $LOCAL_BIN (no sudo)"
  if ! command -v curl >/dev/null 2>&1; then
    die "curl not on PATH. Install it first (apt install curl) — that's Phase C2.2's job."
  fi
  curl -LsSf --fail https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR="$LOCAL_BIN" UV_NO_MODIFY_PATH=1 sh
fi

# ---------------------------------------------------------------------------
# Verification — every check must pass or we die loudly.
# ---------------------------------------------------------------------------
log ""
log "===== Verification ====="
command -v uv >/dev/null 2>&1 || die "uv not on PATH after install ($LOCAL_BIN)"
uv --version

[[ -d "$PUBLISHER_DIR" ]] || die "$PUBLISHER_DIR missing"
[[ -d "$POSTIZ_DIR" ]]    || die "$POSTIZ_DIR missing"
[[ -d "$LOG_DIR" ]]       || die "$LOG_DIR missing"
[[ -f "$ENV_PATH" ]]      || die "$ENV_PATH missing"
env_perms="$(stat -c %a "$ENV_PATH")"
[[ "$env_perms" == "600" ]] || die ".env perms are $env_perms, expected 600"

[[ -d "$BUTLER_BRAIN" ]]  || die "BUTLER_BRAIN disappeared — halt, investigate"
BUTLER_BRAIN_MTIME_AFTER="$(stat -c %Y "$BUTLER_BRAIN" 2>/dev/null || echo "unknown")"
if [[ "$BUTLER_BRAIN_MTIME_BEFORE" != "$BUTLER_BRAIN_MTIME_AFTER" ]]; then
  die "BUTLER_BRAIN mtime changed ($BUTLER_BRAIN_MTIME_BEFORE → $BUTLER_BRAIN_MTIME_AFTER). Halt — something touched it."
fi

log "✅ $PUBLISHER_DIR exists"
log "✅ $POSTIZ_DIR exists"
log "✅ $LOG_DIR exists"
log "✅ $ENV_PATH exists (0600)"
log "✅ uv on PATH ($(command -v uv))"
log "✅ ~/ninja-butler-brain untouched (mtime $BUTLER_BRAIN_MTIME_BEFORE)"
log ""
log "Phase C2.1 bootstrap complete. Ready for C2.2 (sudo syspkg for Playwright deps)."
log "Open a new shell or run:  source ~/.bashrc   to pick up the PATH change."
