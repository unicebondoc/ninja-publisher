# Phase C2.1 review — `scripts/butler/c2-1-bootstrap.sh`

**Purpose:** Prepare Butler's `$HOME` for the ninja-publisher code deploy. No sudo, idempotent, won't touch any live Butler systems.

Boss reads this doc, then pastes the script into her Butler SSH session once comfortable.

---

## What the script does

### Guards (before anything else)
| Lines | Check |
|-------|-------|
| 22–28 | **BUTLER_BRAIN guard.** Aborts if `~/ninja-butler-brain/` is missing. This is the live OpenClaw workspace. |
| 29    | Captures `~/ninja-butler-brain/` mtime into `BUTLER_BRAIN_MTIME_BEFORE` for a final before/after assertion. |
| 35–38 | **Refuse to run as root.** Root's `$HOME` is `/root`; running as root would scatter dirs in the wrong place. |

### Writes
| Step | Path touched | Operation |
|------|--------------|-----------|
| 1 | `~/ninja-clan/ninja-publisher/`      | `mkdir -p` (idempotent) |
| 1 | `~/ninja-clan/ninja-publisher/logs/` | `mkdir -p` (idempotent) |
| 1 | `~/ninja-clan/ninja-publisher/.env`  | **Only if missing.** Created empty with perms `0600` (umask 077). If a real `.env` is already there, left alone. |
| 2 | `~/.local/bin/` | `mkdir -p` |
| 2 | `~/.bashrc` | Appends a marker line + `export PATH="$HOME/.local/bin:$PATH"` — **only once**. Guard via `grep -Fq "$MARKER"`. |
| 3 | `~/.local/bin/uv`, `~/.local/bin/uvx` | From `astral.sh/uv/install.sh`, with `UV_INSTALL_DIR=~/.local/bin` and `UV_NO_MODIFY_PATH=1` so the installer doesn't double-edit `.bashrc`. Only runs if `uv` isn't on PATH already. |
| 4 | `~/ninja-clan/ninja-publisher/.venv/` | `uv venv .venv --python 3.11`. uv uses the highest 3.11+ Python it can find on the box; on Butler that's the system Python 3.12.3. Only runs if `.venv/bin/python` is missing. |

### Reads only (no writes)
- `~/ninja-butler-brain/` — only `stat` to check existence + mtime
- `$EUID` / `id -u` — root check
- `$PATH`, `$HOME`, `$BASHRC` — env lookups
- `curl` — to fetch the uv installer (network only, no disk outside `~/.local/`)

### Explicitly untouched
- `~/ninja-butler-brain/` — never written, never cd'd into, never passed to `rm`/`mv`/`git`/etc.
- `~/.openclaw/` — not referenced anywhere in the script
- `~/n8n-claw/` — not referenced anywhere in the script
- `/var`, `/etc`, `/usr`, `/opt` — system paths not touched (no sudo available anyway)
- Any Docker container, image, or network — no `docker` commands
- Existing `~/.env` (home-level), `~/.gitconfig`, `~/.cloudflared` — not touched

---

## The 7 success markers

Boss's verification checklist maps 1:1 to the `[c2-1] ✅` lines the script prints at the end:

1. **uv installed to `~/.local/bin/uv`** → `✅ uv on PATH (…)`
2. **`~/.local/bin` in PATH via `.bashrc` (exactly one line)** → `✅ ~/.local/bin on PATH via ~/.bashrc (exactly 1 marker line)`
3. **`~/ninja-clan/ninja-publisher/` exists** → `✅ /home/uniceadmin/ninja-clan/ninja-publisher exists`
4. **`.venv` created with Python 3.11+** → `✅ …/.venv created with python 3.12.3` (or whatever ≥3.11 uv picks)
5. **`.env` stub created at 0600** → `✅ …/.env exists (0600)`
6. **Butler Brain mtime unchanged** → `✅ ~/ninja-butler-brain untouched (mtime NNNNNNNNNN)`
7. **All ✅ markers printed** → `✅ All markers green, 0 ❌`

The verification section `die`s on the first failure — no partial success state is possible. If you see `[c2-1] ❌ …` on stderr, the exit code is non-zero and nothing else ran after it.

---

## Expected terminal output

Run 1 (fresh state):

```
[c2-1] Scaffolding /home/uniceadmin/ninja-clan/ninja-publisher …
[c2-1] Creating empty /home/uniceadmin/ninja-clan/ninja-publisher/.env (perms 0600)
[c2-1] Appending ~/.local/bin to PATH in /home/uniceadmin/.bashrc (first run)
[c2-1] Installing uv → /home/uniceadmin/.local/bin (no sudo)
downloading uv 0.11.X x86_64-unknown-linux-gnu
installing to /home/uniceadmin/.local/bin
  uv
  uvx
everything's installed!
[c2-1] Creating /home/uniceadmin/ninja-clan/ninja-publisher/.venv with python 3.11+
Using CPython 3.12.3 interpreter at: /usr/bin/python3
Creating virtual environment at: /home/uniceadmin/ninja-clan/ninja-publisher/.venv
Activate with: source /home/uniceadmin/ninja-clan/ninja-publisher/.venv/bin/activate
[c2-1]
[c2-1] ===== Verification =====
uv 0.11.X (…)
[c2-1] ✅ uv on PATH (/home/uniceadmin/.local/bin/uv)
[c2-1] ✅ ~/.local/bin on PATH via ~/.bashrc (exactly 1 marker line)
[c2-1] ✅ /home/uniceadmin/ninja-clan/ninja-publisher exists
[c2-1] ✅ /home/uniceadmin/ninja-clan/ninja-publisher/.venv created with python 3.12.3
[c2-1] ✅ /home/uniceadmin/ninja-clan/ninja-publisher/.env exists (0600)
[c2-1] ✅ ~/ninja-butler-brain untouched (mtime NNNNNNNNNN)
[c2-1] ✅ All markers green, 0 ❌
[c2-1]
[c2-1] Phase C2.1 bootstrap complete. Ready for C2.2 (sudo syspkg for Playwright deps).
[c2-1] Open a new shell or run:  source ~/.bashrc   to pick up the PATH change.
```

Run 2+ (idempotent re-run):

```
[c2-1] Scaffolding …/ninja-publisher …
[c2-1] …/.env already exists — leaving it alone
[c2-1] ~/.bashrc already has the c2-1 PATH marker — skipping append
[c2-1] uv already present: uv 0.11.X (…)
[c2-1] …/.venv already present — leaving it alone
[c2-1]
[c2-1] ===== Verification =====
…
```

`.bashrc` marker count stays at **1** across re-runs (verified by the explicit marker-count assertion before printing ✅ #2).

---

## Local Docker test (proof it runs clean)

Tested in fresh `ubuntu:24.04` container as non-root user with a fake `~/ninja-butler-brain/`. Two back-to-back runs:

- **Run 1 (fresh):** all 4 writes happened; 7/7 ✅ markers fired. uv installed, `.venv` created with Python 3.12 fetched by uv.
- **Run 2 (idempotent):** every guard fired — `.env already exists`, `marker — skipping append`, `uv already present`, `.venv already present`. Marker count still `1`. 7/7 ✅ fired again.
- **Brain mtime:** identical pre- and post-run.

Boss can reproduce with:

```bash
cd ~/Projects/Personal/ninja-publisher
docker run --rm -v "$PWD/scripts:/scripts:ro" ubuntu:24.04 bash -c '
  apt-get update -qq && apt-get install -y -qq curl sudo
  useradd -m tester && mkdir /home/tester/ninja-butler-brain
  chown -R tester:tester /home/tester
  sudo -u tester bash /scripts/butler/c2-1-bootstrap.sh
'
```

---

## Run instructions for boss

```bash
# 1. Pull the branch (not merged yet) and cat the script before running:
ssh uniceadmin@100.113.62.124
cd /tmp
rm -rf ninja-publisher-staging
git clone https://github.com/unicebondoc/ninja-publisher.git ninja-publisher-staging
cd ninja-publisher-staging

# 2. Read it (trust but verify):
less scripts/butler/c2-1-bootstrap.sh

# 3. Run it:
bash scripts/butler/c2-1-bootstrap.sh 2>&1 | tee /tmp/c2-1-run.log

# 4. Pick up the new PATH:
source ~/.bashrc
which uv && uv --version

# 5. Sanity-check the venv manually:
~/ninja-clan/ninja-publisher/.venv/bin/python --version

# 6. Paste /tmp/c2-1-run.log to Slack #moji-diary.
```

---

## Rollback (if something goes sideways)

Everything this script creates is scoped to two paths and reversible with three commands:

```bash
# 1. Remove the ~/ninja-clan scaffold (only touches files this script created)
rm -rf ~/ninja-clan/ninja-publisher   # takes .venv + logs + .env with it

# 2. Uninstall uv (user-install only, no system state)
rm -f ~/.local/bin/uv ~/.local/bin/uvx
# ~/.local/bin stays — other Butler tools might live there

# 3. Revert the .bashrc addition
# The block is tagged with a marker comment. Remove it:
sed -i '/# ninja-publisher\/c2-1-bootstrap: PATH for ~\/\.local\/bin/,/^export PATH="\$HOME\/\.local\/bin:\$PATH"$/d' ~/.bashrc
# Verify there are no stray duplicates before restarting shell:
grep -n 'ninja-publisher\|\.local/bin' ~/.bashrc
```

What rollback will **not** do:
- Touch `~/ninja-butler-brain/` — it's been untouched all along, and rollback won't touch it either
- Touch `~/.env` at `$HOME` level (the Butler-wide one)
- Kill any Docker container or n8n-claw service
- Remove existing PATH manipulations from anyone else's tool (asdf, nvm, .cursor, etc.)

After rollback, the box is in its exact pre-C2.1 state.

---

## Not in this script (deferred to later sub-phases)

- **C2.2:** `sudo apt install` of Playwright Chromium deps (libnss3, libatk1.0-0, libxkbcommon0, libasound2t64, libgbm1, etc.). Separate script, boss runs interactively with `sudo`.
- **C2.3:** `uv pip install -r requirements.txt` against the `.venv` this script created + `playwright install chromium`. Boss runs when ready.

Everything in this script is 100 % reversible in under 10 seconds. Nothing persistent if rollback runs.
