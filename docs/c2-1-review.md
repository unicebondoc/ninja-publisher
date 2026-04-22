# Phase C2.1 review — `scripts/butler/c2-1-bootstrap.sh`

**Purpose:** Prepare Butler's `$HOME` for the ninja-publisher code deploy. No sudo, idempotent, won't touch any live Butler systems.

Boss reads this doc, then pastes the script into her Butler SSH session once comfortable.

---

## What the script does

### Guards (before anything else)
| Lines | Check |
|-------|-------|
| 23–27 | **BUTLER_BRAIN guard.** Aborts if `~/ninja-butler-brain/` is missing. This is the live OpenClaw workspace. |
| 28    | Captures `~/ninja-butler-brain/` mtime into `BUTLER_BRAIN_MTIME_BEFORE` for a final before/after assertion. |
| 34–37 | **Refuse to run as root.** Root's `$HOME` is `/root`; running this as root would scatter dirs in the wrong place. |

### Writes
| Step | Path touched | Operation |
|------|--------------|-----------|
| 1 | `~/ninja-clan/butler/ninja-publisher/` | `mkdir -p` (idempotent) |
| 1 | `~/ninja-clan/postiz-app/`             | `mkdir -p` (idempotent) |
| 1 | `~/ninja-clan/butler/ninja-publisher/logs/` | `mkdir -p` (idempotent) |
| 1 | `~/ninja-clan/butler/ninja-publisher/.env` | **Only if missing.** Created empty with perms `0600` (umask 077). If a real `.env` is already there, left alone. |
| 2 | `~/.local/bin/` | `mkdir -p` |
| 2 | `~/.bashrc` | Appends a marker line + `export PATH="$HOME/.local/bin:$PATH"` — **only once**. Guard via `grep -Fq "$MARKER"`. |
| 3 | `~/.local/bin/uv`, `~/.local/bin/uvx` | From `astral.sh/uv/install.sh`, with `UV_INSTALL_DIR=~/.local/bin` and `UV_NO_MODIFY_PATH=1` so the installer doesn't double-edit `.bashrc`. Only runs if `uv` isn't on PATH already. |

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
- Any Docker container or image — no `docker` commands
- Existing `~/.env` (home-level), `~/.gitconfig`, `~/.cloudflared` — not touched

---

## Expected terminal output

Run 1 (fresh state):

```
[c2-1] Scaffolding /home/uniceadmin/ninja-clan …
[c2-1] Creating empty /home/uniceadmin/ninja-clan/butler/ninja-publisher/.env (perms 0600)
[c2-1] Appending ~/.local/bin to PATH in /home/uniceadmin/.bashrc (first run)
[c2-1] Installing uv → /home/uniceadmin/.local/bin (no sudo)
downloading uv 0.11.X x86_64-unknown-linux-gnu
installing to /home/uniceadmin/.local/bin
  uv
  uvx
everything's installed!
[c2-1]
[c2-1] ===== Verification =====
uv 0.11.X (x86_64-unknown-linux-gnu)
[c2-1] ✅ /home/uniceadmin/ninja-clan/butler/ninja-publisher exists
[c2-1] ✅ /home/uniceadmin/ninja-clan/postiz-app exists
[c2-1] ✅ /home/uniceadmin/ninja-clan/butler/ninja-publisher/logs exists
[c2-1] ✅ /home/uniceadmin/ninja-clan/butler/ninja-publisher/.env exists (0600)
[c2-1] ✅ uv on PATH (/home/uniceadmin/.local/bin/uv)
[c2-1] ✅ ~/ninja-butler-brain untouched (mtime NNNNNNNNNN)
[c2-1]
[c2-1] Phase C2.1 bootstrap complete. Ready for C2.2 (sudo syspkg for Playwright deps).
[c2-1] Open a new shell or run:  source ~/.bashrc   to pick up the PATH change.
```

Run 2+ (idempotent re-run):

```
[c2-1] Scaffolding /home/uniceadmin/ninja-clan …
[c2-1] /home/uniceadmin/ninja-clan/butler/ninja-publisher/.env already exists — leaving it alone
[c2-1] ~/.bashrc already has the c2-1 PATH marker — skipping append
[c2-1] uv already present: uv 0.11.X (x86_64-unknown-linux-gnu) (/home/uniceadmin/.local/bin/uv)
[c2-1]
[c2-1] ===== Verification =====
…
```

If `set -e` fires, the script aborts with the offending command's output and exit ≠ 0. The verification section's `die` wrapper prints `[c2-1] ❌ <reason>` to stderr.

---

## Local Docker test (proof it runs clean)

I tested this in a fresh `ubuntu:24.04` container under a non-root user with a fake `~/ninja-butler-brain/`:

- **Run 1 (fresh):** uv 0.11.7 installed to `~/.local/bin`, all four target dirs created, `.env` perms `600`, `.bashrc` marker added. ✅
- **Run 2 (idempotent):** every guard fired correctly — `.env already exists`, `c2-1 PATH marker — skipping append`, `uv already present`. Marker count in `.bashrc` stayed at **1** (no duplicates). ✅
- **BUTLER_BRAIN mtime assertion:** after Run 1, `stat -c %Y` on the brain directory still showed the pre-run mtime (1767225600 in the test rig). ✅

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
cd ~
curl -sSfL https://raw.githubusercontent.com/unicebondoc/ninja-publisher/phase-c2-1-bootstrap/scripts/butler/c2-1-bootstrap.sh > /tmp/c2-1-bootstrap.sh

# 2. Read it (trust but verify):
less /tmp/c2-1-bootstrap.sh

# 3. Run it:
bash /tmp/c2-1-bootstrap.sh

# 4. Pick up the new PATH:
source ~/.bashrc
which uv && uv --version

# 5. Screenshot or paste the output back to me so I know we're green.
```

---

## Rollback (if something goes sideways)

Everything this script creates is scoped to three paths and reversible with three commands:

```bash
# 1. Remove the ~/ninja-clan scaffold (only touches files this script created)
rm -rf ~/ninja-clan/butler ~/ninja-clan/postiz-app

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

- **C2.2:** `sudo apt install` of Playwright Chromium deps (libnss3, libatk1.0-0, libxkbcommon0, libasound2, libgbm1, etc.). Separate script, boss runs interactively with `sudo`.
- **C2.3:** Postiz docker compose + dedicated Postgres + nginx vhost on `postiz.ninja-clan.ts.net` (Tailscale-only).
- **C2.4:** `uv sync` / `pip install -r requirements.txt` + `playwright install chromium`.

Everything in this script is 100 % reversible in under 10 seconds. Nothing persistent if rollback runs.
