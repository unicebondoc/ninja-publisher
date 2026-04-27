# Phase C2.2 review — `scripts/butler/c2-2-syspkg.sh`

**Purpose:** Install the 14 system libraries Playwright's Chromium binary needs at runtime on Ubuntu 24.04. Requires `sudo`. Idempotent. No writes under `$HOME`.

Boss reads this doc, then runs the script in her Butler SSH session — she'll be prompted for her sudo password once.

---

## What the script does

### Guards (before anything else)
| Lines | Check |
|-------|-------|
| 24–28 | **BUTLER_BRAIN guard.** Aborts if `~/ninja-butler-brain/` is missing. |
| 29    | Captures `~/ninja-butler-brain/` mtime into `BUTLER_BRAIN_MTIME_BEFORE`. `apt-get` writes to `/var/*` and `/usr/*`, never `$HOME`, so this mtime should remain identical — if it changes, something went *deeply* wrong and we halt. |
| 36–39 | **Refuse to run as root directly.** Boss runs as normal user; `sudo` is invoked internally on the two `apt-get` lines only. This keeps `$HOME = /home/uniceadmin` (so the brain guard resolves correctly) and limits root's blast radius to apt's commands. |

### The 14 packages — and why each one

All 14 are required for Playwright Chromium per Playwright's own [Linux dependencies list](https://playwright.dev/docs/browsers#install-browsers). The `t64` suffix is Noble's 64-bit `time_t` transition — the un-suffixed names of affected libs exist only as **transitional dummy packages** that re-point to the real `*t64` package. Calling `dpkg-query` on a dummy name returns "not installed" even if the real lib is there, so this script targets the `t64` variants directly:

| Package | Why Chromium needs it |
|---------|-----------------------|
| `libnss3` | NSS crypto (TLS) |
| `libatk-bridge2.0-0t64` | AT-SPI accessibility bridge — **t64 on Noble** (un-suffixed is a transitional dummy) |
| `libatk1.0-0t64` | ATK accessibility toolkit — **t64 on Noble** |
| `libxkbcommon0` | XKB keyboard layouts |
| `libasound2t64` | ALSA audio — **t64** |
| `libgbm1` | GBM for GPU buffer management (headless Chromium) |
| `libcups2t64` | CUPS printing subsystem (Chrome calls into it even headless) — **t64 on Noble** |
| `libpango-1.0-0` | Pango text layout |
| `libgtk-3-0t64` | GTK3 for widgets — **t64** |
| `libwoff1` | WOFF web-font decoding |
| `libharfbuzz-icu0` | HarfBuzz + ICU text shaping |
| `libgstreamer-plugins-base1.0-0` | GStreamer for `<video>`/`<audio>` element decoding |
| `libvpx9` | VP9 video codec |
| `libevent-2.1-7t64` | libevent for async I/O — **t64** |

**Deviation from your originally approved list:** you approved 14 packages with three un-suffixed names (`libatk-bridge2.0-0`, `libatk1.0-0`, `libcups2`). The Docker test caught these as transitional dummies on Noble — `apt-get install libatk-bridge2.0-0` does pull in the real `libatk-bridge2.0-0t64` (so Playwright still works), but `dpkg-query` on the un-suffixed name reports "not installed", which made my verification loop false-negative 3 of 14 on every run. Targeting the `t64` variants directly fixes the verifier and is the form actually persisted in `/var/lib/dpkg/status`.

### Writes
| Target | Operation |
|--------|-----------|
| `/var/lib/apt/lists/*` | `sudo apt-get update` refreshes the package index. Normal apt operation. |
| `/var/cache/apt/archives/*` | Downloaded `.deb` files live here. Apt manages cleanup. |
| `/var/lib/dpkg/status` | Dpkg records the newly installed packages. |
| `/usr/lib/*`, `/usr/share/*`, `/etc/alternatives/*` | Library files + symlinks installed by each `.deb`. |

### Explicitly untouched
- `$HOME/ninja-butler-brain/` — only `stat` to check existence + mtime
- `$HOME/.openclaw/`, `$HOME/n8n-claw/`, `$HOME/.bashrc`, `$HOME/.local/`, `$HOME/ninja-clan/` — never referenced
- Docker / containers / networks — no `docker` commands
- Third-party PPAs / sources — only Ubuntu's official `noble`, `noble-updates`, `noble-security` repos are used (whatever apt already had configured before we showed up). No `add-apt-repository` calls.

---

## Security

- **No PPAs, no third-party sources.** The script calls `apt-get update` + `apt-get install`; whatever repos Ubuntu already had enabled is what's used. If you want to verify before running: `apt-cache policy` on Butler shows the active sources.
- **No env-var passthrough to sudo.** Butler's sudoers uses `env_reset` with a minimal `env_keep` — passing `VAR=value` before `sudo` is rejected and exits the script via `set -e`. This script never prefixes env vars on a sudo line; `apt-get install -y` with no TTY on stdin is non-interactive enough for these 14 libs (no debconf prompts).
- **No `curl | bash`.** Unlike C2.1's uv installer, this phase only uses apt.
- **No shell expansions with user input.** `${MISSING[*]}` expands to the hardcoded `PKGS` list intersected with what's already installed — no user-controlled strings reach the `apt-get` args.

---

## Expected terminal output

### Run 1 (fresh box, nothing installed)

```
[c2-2] Requested: 14 packages
[c2-2] Already installed: 0
[c2-2] Missing (will install): 14
[c2-2] Missing: libnss3 libatk-bridge2.0-0t64 libatk1.0-0t64 libxkbcommon0 libasound2t64 libgbm1 libcups2t64 libpango-1.0-0 libgtk-3-0t64 libwoff1 libharfbuzz-icu0 libgstreamer-plugins-base1.0-0 libvpx9 libevent-2.1-7t64
[c2-2] Running: sudo apt-get update
[sudo] password for uniceadmin:
Hit:1 http://archive.ubuntu.com/ubuntu noble InRelease
...
[c2-2] Running: sudo apt-get install -y libnss3 libatk-bridge2.0-0 …
Reading package lists... Done
Building dependency tree... Done
The following additional packages will be installed:
  …
The following NEW packages will be installed:
  libatk-bridge2.0-0 libatk1.0-0 libasound2t64 libcups2 libevent-2.1-7t64
  libgbm1 libgstreamer-plugins-base1.0-0 libgtk-3-0t64 libharfbuzz-icu0
  libnss3 libpango-1.0-0 libvpx9 libwoff1 libxkbcommon0
0 upgraded, 14 newly installed, 0 to remove and X not upgraded.
Need to get N MB of archives.
…
[c2-2]
[c2-2] ===== Verification =====
[c2-2] ✅ libnss3
[c2-2] ✅ libatk-bridge2.0-0t64
[c2-2] ✅ libatk1.0-0t64
[c2-2] ✅ libxkbcommon0
[c2-2] ✅ libasound2t64
[c2-2] ✅ libgbm1
[c2-2] ✅ libcups2t64
[c2-2] ✅ libpango-1.0-0
[c2-2] ✅ libgtk-3-0t64
[c2-2] ✅ libwoff1
[c2-2] ✅ libharfbuzz-icu0
[c2-2] ✅ libgstreamer-plugins-base1.0-0
[c2-2] ✅ libvpx9
[c2-2] ✅ libevent-2.1-7t64
[c2-2] ✅ ~/ninja-butler-brain untouched (mtime NNNNNNNNNN)
[c2-2]
[c2-2] ✅ All 14 Playwright Chromium deps installed, 0 ❌
[c2-2]
[c2-2] Phase C2.2 syspkg complete. Ready for C2.3 (uv pip install + playwright install chromium).
```

### Run 2 (idempotent — everything already installed)

```
[c2-2] Requested: 14 packages
[c2-2] Already installed: 14
[c2-2] Missing (will install): 0
[c2-2] Nothing to install. Skipping apt-get update.
[c2-2]
[c2-2] ===== Verification =====
[c2-2] ✅ libnss3
… (14 ✅ lines)
[c2-2] ✅ ~/ninja-butler-brain untouched (mtime NNNNNNNNNN)
[c2-2]
[c2-2] ✅ All 14 Playwright Chromium deps installed, 0 ❌
[c2-2]
[c2-2] Phase C2.2 syspkg complete. Ready for C2.3 (uv pip install + playwright install chromium).
```

Note: the idempotent re-run does **not** call `apt-get update` — there's nothing to install, so we save Butler a network round-trip.

---

## Run instructions for boss

Assuming staging is already cloned at `/tmp/ninja-publisher-staging/` (Miji handles that in the pre-flight step):

```bash
ssh uniceadmin@100.113.62.124
bash /tmp/ninja-publisher-staging/scripts/butler/c2-2-syspkg.sh 2>&1 | tee /tmp/c2-2-run.log
# sudo prompt for password — once — for apt-get update + install
```

Then paste `/tmp/c2-2-run.log` back.

---

## Rollback

**Preferred: don't roll back.** These 14 packages are generic shared libraries (NSS, ATK, GTK, GStreamer, Pango, etc.). Many other Ubuntu programs depend on them transitively. `apt` will refuse to auto-remove any that other packages still need, but removing libraries shared with Butler's existing services (nginx, cron scripts, ollama, docker containers' host-side helpers) could destabilize them.

If you *must* roll back (e.g. one of them broke something):

```bash
# Check what else depends on a package before removing:
apt-cache rdepends --installed libasound2t64

# Only if the rdepends list is empty or contains only the packages you just
# installed together, then it's safe to remove:
sudo apt-get autoremove libnss3 libatk-bridge2.0-0t64 libatk1.0-0t64 libxkbcommon0 \
  libasound2t64 libgbm1 libcups2t64 libpango-1.0-0 libgtk-3-0t64 libwoff1 \
  libharfbuzz-icu0 libgstreamer-plugins-base1.0-0 libvpx9 libevent-2.1-7t64
```

`autoremove` is the safer verb here — it won't remove anything still depended upon.

After this, Playwright Chromium will refuse to launch (missing shared libs) and the ninja-publisher dispatcher will fail on any page-render step. That's the expected state post-rollback.

---

## Not in this script (deferred)

- **C2.3:** `uv pip install -r requirements.txt` against the `.venv` C2.1 created, then `playwright install chromium` (downloads the browser binary to `~/.cache/ms-playwright/`). No sudo.
- **Dispatcher / cron / systemd:** later sub-phase.
