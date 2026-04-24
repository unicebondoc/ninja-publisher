#!/usr/bin/env bash
# ninja-publisher — Phase C2.1 bootstrap (Butler VPS, no sudo)
#
# Idempotent. Writes only under:
#   - $HOME/ninja-clan/ninja-publisher/   (project target dir + .venv + .env)
#   - $HOME/.local/                       (uv binary via user install)
#   - $HOME/.bashrc                       (appends PATH export once)
#
# Never reads from, writes to, or cd's into:
#   - $HOME/ninja-butler-brain/  (Butler's live brain)
#   - $HOME/.openclaw/           (Butler's active workspace)
#   - $HOME/n8n-claw/            (n8n-claw container stack dir — unrelated to us)
#   - /var, /etc, /usr, any system path
#
# Meant to be `bash scripts/butler/c2-1-bootstrap.sh` — boss runs it by
# hand in her Butler SSH session after reviewing docs/c2-1-review.md.

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
# Guard 2: refuse to run as root. $HOME would point at /root.
# ---------------------------------------------------------------------------
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "❌ This script must run as a normal user (not root)." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NINJA_ROOT="$HOME/ninja-clan"
PUBLISHER_DIR="$NINJA_ROOT/ninja-publisher"
LOG_DIR="$PUBLISHER_DIR/logs"
VENV_DIR="$PUBLISHER_DIR/.venv"
ENV_PATH="$PUBLISHER_DIR/.env"
LOCAL_BIN="$HOME/.local/bin"
BASHRC="$HOME/.bashrc"
MARKER="# ninja-publisher/c2-1-bootstrap: PATH for ~/.local/bin"
PY_REQUEST="3.11"  # uv will use system python3.11+ if available, else fetch

log()  { printf '[c2-1] %s\n' "$*"; }
die()  { printf '[c2-1] ❌ %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Step 1: scaffold ~/ninja-clan/ninja-publisher/{logs}
# ---------------------------------------------------------------------------
log "Scaffolding $PUBLISHER_DIR …"
mkdir -p "$PUBLISHER_DIR" "$LOG_DIR"

# Secrets hygiene: pre-create an empty .env with 0600 perms. Skip if present.
if [[ ! -e "$ENV_PATH" ]]; then
  log "Creating empty $ENV_PATH (perms 0600)"
  ( umask 077 && : > "$ENV_PATH" )
else
  log "$ENV_PATH already exists — leaving it alone"
fi

# ---------------------------------------------------------------------------
# Step 2: ensure ~/.local/bin exists and is on PATH permanently (.bashrc)
# ---------------------------------------------------------------------------
mkdir -p "$LOCAL_BIN"
touch "$BASHRC"

if grep -Fq "$MARKER" "$BASHRC"; then
  log "~/.bashrc already has the c2-1 PATH marker — skipping append"
else
  log "Appending ~/.local/bin to PATH in $BASHRC (first run)"
  {
    printf '\n%s\n' "$MARKER"
    printf '%s\n' 'export PATH="$HOME/.local/bin:$PATH"'
  } >> "$BASHRC"
fi

export PATH="$LOCAL_BIN:$PATH"

# ---------------------------------------------------------------------------
# Step 3: install uv (user install, no sudo, pinned install dir)
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
# Step 4: create the project venv with Python 3.11+ (uv manages Python for us)
# ---------------------------------------------------------------------------
if [[ -x "$VENV_DIR/bin/python" ]]; then
  log "$VENV_DIR already present — leaving it alone"
else
  log "Creating $VENV_DIR with python $PY_REQUEST+"
  ( cd "$PUBLISHER_DIR" && uv venv "$VENV_DIR" --python "$PY_REQUEST" )
fi

# ---------------------------------------------------------------------------
# Verification — every check must pass or we die loudly.
# ---------------------------------------------------------------------------
log ""
log "===== Verification ====="

command -v uv >/dev/null 2>&1 || die "uv not on PATH after install ($LOCAL_BIN)"
uv --version

[[ -d "$PUBLISHER_DIR" ]] || die "$PUBLISHER_DIR missing"
[[ -d "$LOG_DIR" ]]       || die "$LOG_DIR missing"
[[ -f "$ENV_PATH" ]]      || die "$ENV_PATH missing"
env_perms="$(stat -c %a "$ENV_PATH")"
[[ "$env_perms" == "600" ]] || die ".env perms are $env_perms, expected 600"

[[ -x "$VENV_DIR/bin/python" ]] || die "$VENV_DIR/bin/python missing"
venv_py_ver="$("$VENV_DIR/bin/python" --version 2>&1 | awk '{print $2}')"
if ! [[ "$venv_py_ver" =~ ^3\.(11|12|13|14)(\..*)?$ ]]; then
  die "venv python is $venv_py_ver, expected 3.11+"
fi

# Boss's marker 2: bashrc must have exactly one line referencing our marker
bashrc_marker_count="$(grep -Fc "$MARKER" "$BASHRC" || true)"
[[ "$bashrc_marker_count" == "1" ]] || die ".bashrc has $bashrc_marker_count marker lines, expected 1"

[[ -d "$BUTLER_BRAIN" ]]  || die "BUTLER_BRAIN disappeared — halt, investigate"
BUTLER_BRAIN_MTIME_AFTER="$(stat -c %Y "$BUTLER_BRAIN" 2>/dev/null || echo "unknown")"
if [[ "$BUTLER_BRAIN_MTIME_BEFORE" != "$BUTLER_BRAIN_MTIME_AFTER" ]]; then
  die "BUTLER_BRAIN mtime changed ($BUTLER_BRAIN_MTIME_BEFORE → $BUTLER_BRAIN_MTIME_AFTER). Halt — something touched it."
fi

log "✅ uv on PATH ($(command -v uv))"
log "✅ ~/.local/bin on PATH via ~/.bashrc (exactly 1 marker line)"
log "✅ $PUBLISHER_DIR exists"
log "✅ $VENV_DIR created with python $venv_py_ver"
log "✅ $ENV_PATH exists (0600)"
log "✅ ~/ninja-butler-brain untouched (mtime $BUTLER_BRAIN_MTIME_BEFORE)"
log "✅ All markers green, 0 ❌"
log ""
log "Phase C2.1 bootstrap complete. Ready for C2.2 (sudo syspkg for Playwright deps)."
log "Open a new shell or run:  source ~/.bashrc   to pick up the PATH change."
