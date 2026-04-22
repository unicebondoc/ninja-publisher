# Butler VPS audit — 2026-04-22

**Run by:** Claude Code (Miji) from Warrior
**Target:** `uniceadmin@100.113.62.124` (hostname `ninja-clan`)
**Mode:** READ-ONLY. No installs, no systemctl changes, no file writes on VPS.
**SSH:** BatchMode=yes so any `sudo` requiring password fails fast (see "sudo caveat" below).

---

## Executive summary

Butler is a well-provisioned Ubuntu 24.04 LTS box (kernel 6.8) with 7.6 GB RAM, 4 cores, 100 GB free disk. It's already running a full **n8n-claw stack under Docker** (Supabase Postgres + Kong + Studio + crawl4ai + searxng + n8n + email bridge + 2 MiniMax MCP containers) and has **nginx + fail2ban + cloudflared + Tailscale** all active. Boss's existing cron-based Butler jobs (check-ins, meds, diary, backup, morning briefing, calendar sync, music) live in `~/.openclaw/workspace/cron/`.

Phase C2 will land cleanly, but there are **six decisions needed from boss** before I write a single byte to this box (see "Decisions required" section).

---

## Decisions required before Phase C2

| # | Question | Options | My recommendation |
|---|----------|---------|-------------------|
| 1 | **Postgres for Postiz** | (a) Reuse the existing Supabase container `n8n-claw-db` on 127.0.0.1:5432 — create a dedicated `postiz` DB inside it. (b) Spin up a *separate* Postgres via Postiz's own compose file. | **(b) fresh Postgres.** `n8n-claw-db` has its own Supabase auth/storage schemas; Postiz's migrations could collide or leave footprints that complicate disaster recovery. 100 GB free, Postiz max DB ≈ 5 GB, so isolation is cheap. |
| 2 | **Reverse proxy** | Spec said "add Caddy"; nginx is already active + fail2ban-protected. (a) Swap to Caddy. (b) Keep nginx, add a Postiz vhost. | **(b) keep nginx.** fail2ban already has `nginx-http-auth` + `nginx-limit-req` jails tuned; ripping it out is a regression. I'll add a postiz.ninja-clan.ts.net Tailscale-only nginx vhost proxying → localhost:5000. |
| 3 | **Target directory** | Spec said `~/ninja-clan/` — but that dir is currently empty. `~/mempalace_ninja_clan/` holds an older `cron/` + `content/` archive. | **Use `~/ninja-clan/`** (empty, matches spec). Put `postiz-app/` and `butler/ninja-publisher/` under it as planned. |
| 4 | **Secrets path** | Spec said `~/.secrets/` but it doesn't exist. Butler's current pattern is a single `~/.env` (perms 0600). No `~/.config/ninja` either. | **Use `~/ninja-clan/butler/ninja-publisher/.env`** (colocated, 0600, gitignored). Matches Postiz's own `.env` convention. A separate `~/.secrets/` adds a layer for no benefit on a single-tenant VPS. |
| 5 | **sudo access** | Interactive `sudo` requires a password on this box; non-interactive `sudo -n` all fail. | Boss will need to **run the install steps interactively** (or grant temp passwordless sudo for the apt/docker steps). I will pre-script each sudo command and ask boss to paste & confirm rather than sudoing blind. |
| 6 | **Python runtime** | System Python is 3.12.3; `pip3` is **not** on PATH. No `pipx` either. | **Install uv** (single static binary, no sudo if we use the user install) or `apt install python3-pip python3-venv`. uv is cleaner — I'll propose the user-install path in C2. |

---

## Key findings

### System baseline ✅
- **OS:** Ubuntu 24.04.4 LTS "noble", kernel `6.8.0-107-generic`
- **CPU:** 4 cores
- **RAM:** 7.6 GB total / 4.7 GB available / **0 B swap** ← watch this under Postiz load
- **Disk:** 150 GB `/` partition, 45 GB used (32 %), **100 GB free**
- **Uptime:** 16 d 19 h (steady)

### Network & firewall ✅
- **Open externally (ufw ALLOW IN):** 22, 80, 443
- **Explicitly denied:** 5678, 3456 (both v4 + v6)
- **Listening on localhost only:** Postgres 5432, cloudflared 20241, studio 3001, searxng 8888, ollama 11434
- **Listening on Tailscale IP only (100.113.62.124):** 8443, 64799
- **Listening on 0.0.0.0:** 22 (ssh), 80 (nginx), 443 (nginx), 3456 (python3 pid 2096008 — denied externally by ufw)
- **Tailscale active:** only `ninja-clan` + `unices-macbook-pro` currently online; 2 devices offline (iphone, uk-imac)

### Docker inventory
- Docker **29.4.1** installed and healthy
- Running containers (10): `n8n-claw-db` (Supabase Postgres 15.8.1), `n8n-claw-kong`, `n8n-claw-meta`, `n8n-claw-studio` (unhealthy), `n8n-claw-crawl4ai` (5.5 GB image — largest), `n8n-claw-searxng`, `n8n-claw-email-bridge`, `n8n-claw-minimax-mcp`, `n8n-claw-minimax-media-mcp`, + `openclaw-sandbox` (exited)
- Stopped: `postgrest/postgrest:v14.5` (exited 1), `n8n-claw` main worker (exited 0 two weeks ago), `openclaw-sandbox` (exited 137)
- Networks: `bridge`, `host`, `n8n-claw_n8n-claw-net`, `none`
- **No `postiz_*` containers or networks yet** — clean slate for Phase C2

### Postgres
- **`n8n-claw-db`** running Supabase Postgres `15.8.1.085`, bound to **127.0.0.1:5432**, status "Up 7 hours (healthy)"
- **No** systemd `postgresql.service` — Postgres is exclusively containerised
- **No** `/var/lib/postgresql` on the host
- Cannot run `psql -l` without `sudo` password

### Existing Butler services
- **Zero** systemd services matching `butler|ninja|telegram|minimax`
- Everything is cron-based under `~/.openclaw/workspace/cron/`
- Active crontab (user, TZ=Australia/Sydney) runs: check-ins (7/day), backup (12 pm), nightly diary (11:30 pm), medication reminders + pesters (metformin daily, ozempic Mondays), morning briefing (8 am), calendar sync (every 15 min), hype music (8:15 am), lullaby (11:45 pm), voice transcriber (@reboot)
- **Cron load:** heavy at the top of the hour (0 9/12/15/18/20/22/23 all fire `checkin.py`) + a 15-min calendar-sync pulse + 4-pulse per minute medication pester windows. Ninja-publisher dispatcher at `*/5` will fit comfortably.

### Reverse proxy & TLS
- **nginx** `/usr/sbin/nginx` is installed, `active (running)` since 2026-04-13, enabled on boot
- **Caddy** is **not** installed (`Unit caddy.service could not be found`)
- Boss's Tailscale cert `ninja-clan.tail998dd.ts.net.crt` + `.key` live in `/home/uniceadmin/` (owned by root, 644/600)

### Security posture
- **fail2ban** active with 4 jails: `nginx-http-auth`, `nginx-limit-req`, `recidive`, `sshd` ✅
- **ufw** active with least-privilege defaults (deny incoming, deny routed)
- **cloudflared** running on 127.0.0.1:20241 (some tunnel is already configured)
- Root home has the Tailscale cert — sane

### Playwright prerequisites ⚠️
- Only `libxkbcommon0` is installed (1 of the 4+ Chromium needs)
- **Missing:** `libnss3`, `libatk*`, `libasound2` (and their transitive deps)
- Phase C2 will need `sudo apt install` — **boss needs to run this interactively** (see decision #5)

### Secrets layout
- `~/.secrets/` **does not exist**
- `~/.env` **exists** (1289 bytes, perms 0600, modified 2026-04-22 13:22) — boss's existing pattern
- `~/.config/ninja` does not exist

### Target directories
- `~/ninja-clan/` — **empty placeholder** (4 KB, created 2026-04-05) ← spec target
- `~/mempalace_ninja_clan/` — archive (344 KB) with old `cron/` + `content/` + AGENTS/MEMORY/IDENTITY markdown
- `~/.openclaw/workspace/` — active Butler workspace (268 KB, AGENTS.md + ARCHITECTURE.md + cron/ + drawers/ + backfill-diary.py + diary-standard.md)
- `~/ninja-butler-brain/` — **2.9 MB, modified today 14:39** — new? boss should clarify whether this is separate or related

### Live load
- Load avg 0.18/0.16/0.17 — idle-ish
- 205 tasks, 95.7 % idle CPU
- 2.95 GB RAM in use, 3.93 GB buff/cache, 1.23 GB free
- Top process: `top` itself (9 % CPU, transient)

---

## sudo caveat

All commands in the audit script use `sudo -n` (non-interactive). The following commands returned `sudo: a password is required` and did **not** produce output:

- `sudo -n ufw status verbose` — worked (ufw readable without sudo on this box)
- `sudo -n -u postgres psql -l` — failed (`sudo: unknown user postgres` → postgres lives only inside the container, and sudo is locked anyway)
- `sudo -n du -sh /var/lib/docker` — failed
- `sudo -n du -sh /var/log` — failed (partial output: `/var/log` is 923 MB but 3 subdirs are root-only)
- `sudo -n systemctl status caddy` / `nginx` — actually worked (status is queryable without sudo on Ubuntu 24.04)
- `sudo -n fail2ban-client status` — worked

So the only real gaps are **docker disk usage** and **full /var/log depth** — neither blocks Phase C2.

---

## Paper trail: no writes verification

No files were created, modified, or deleted on the VPS during this audit. Commands used:
- `echo`, `date`, `uname`, `whoami`, `hostname`, `lsb_release -a`, `uptime`, `free`, `df`, `nproc`
- `ss -tlnp`, `tailscale status`
- `docker --version`, `docker ps`, `docker images`, `docker network ls`
- `ls`, `du`, `dpkg -l`, `which`
- `systemctl status` (read-only), `systemctl list-units`, `crontab -l`
- `sudo -n …` (non-interactive; failed commands wrote no files)
- `top -bn1`

Boss can verify with: `ssh uniceadmin@100.113.62.124 'ls -la ~/ninja-clan'` → still empty; nothing new under `/tmp/` outside the shell session's transient tmpfiles.

---

## Raw audit transcript

The commands below were executed in a single SSH session via `bash -s` with `BatchMode=yes`. Output is unedited.

````text
===== whoami / hostname / date =====
uniceadmin
ninja-clan
Wed Apr 22 05:48:44 PM AEST 2026
Wed Apr 22 07:48:44 AM UTC 2026


===== uname / lsb_release / uptime / free / df / nproc =====
Linux ninja-clan 6.8.0-107-generic #107-Ubuntu SMP PREEMPT_DYNAMIC Fri Mar 13 19:51:50 UTC 2026 x86_64 x86_64 x86_64 GNU/Linux
Distributor ID:	Ubuntu
Description:	Ubuntu 24.04.4 LTS
Release:	24.04
Codename:	noble
 17:48:44 up 16 days, 19:04,  2 users,  load average: 0.18, 0.16, 0.17
               total        used        free      shared  buff/cache   available
Mem:           7.6Gi       2.9Gi       1.2Gi        54Mi       3.8Gi       4.7Gi
Swap:             0B          0B          0B
Filesystem      Size  Used Avail Use% Mounted on
tmpfs           775M  1.8M  773M   1% /run
efivarfs        256K   27K  225K  11% /sys/firmware/efi/efivars
/dev/sda1       150G   45G  100G  32% /
tmpfs           3.8G     0  3.8G   0% /dev/shm
tmpfs           5.0M     0  5.0M   0% /run/lock
/dev/sda15      253M  146K  252M   1% /boot/efi
tmpfs           775M  1.5M  774M   1% /run/user/1000
4


===== Network: ss -tlnp =====
State  Recv-Q Send-Q               Local Address:Port  Peer Address:PortProcess
LISTEN 0      4096                    127.0.0.54:53         0.0.0.0:*
LISTEN 0      511                        0.0.0.0:18789      0.0.0.0:*    users:(("openclaw-gatewa",pid=569492,fd=22))
LISTEN 0      4096                     127.0.0.1:5432       0.0.0.0:*
LISTEN 0      4096                100.113.62.124:8443       0.0.0.0:*
LISTEN 0      4096                     127.0.0.1:8888       0.0.0.0:*
LISTEN 0      4096                 127.0.0.53%lo:53         0.0.0.0:*
LISTEN 0      128                        0.0.0.0:3456       0.0.0.0:*    users:(("python3",pid=2096008,fd=3))
LISTEN 0      4096                     127.0.0.1:20241      0.0.0.0:*    users:(("cloudflared",pid=3534188,fd=3))
LISTEN 0      4096                100.113.62.124:64799      0.0.0.0:*
LISTEN 0      511                        0.0.0.0:80         0.0.0.0:*
LISTEN 0      4096                       0.0.0.0:22         0.0.0.0:*
LISTEN 0      4096                     127.0.0.1:11434      0.0.0.0:*
LISTEN 0      511                        0.0.0.0:443        0.0.0.0:*
LISTEN 0      4096                     127.0.0.1:3001       0.0.0.0:*
LISTEN 0      4096                          [::]:22            [::]:*
LISTEN 0      4096   [fd7a:115c:a1e0::353b:3e7c]:62729         [::]:*
LISTEN 0      4096   [fd7a:115c:a1e0::353b:3e7c]:8443          [::]:*


===== Firewall: sudo -n ufw status verbose =====
Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), deny (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
80/tcp                     ALLOW IN    Anywhere
443/tcp                    ALLOW IN    Anywhere
5678/tcp                   DENY IN     Anywhere
3456                       DENY IN     Anywhere
22/tcp (v6)                ALLOW IN    Anywhere (v6)
80/tcp (v6)                ALLOW IN    Anywhere (v6)
443/tcp (v6)               ALLOW IN    Anywhere (v6)
5678/tcp (v6)              DENY IN     Anywhere (v6)
3456 (v6)                  DENY IN     Anywhere (v6)


===== Tailscale status =====
100.113.62.124   ninja-clan          uniceabondoc@  linux  -
100.89.112.128   iphone182           uniceabondoc@  iOS    offline, last seen 10d ago
100.109.239.112  uk-imac             uniceabondoc@  linux  offline, last seen 21d ago
100.81.178.125   unices-macbook-pro  uniceabondoc@  macOS  active; direct 180.233.125.158:29353, tx 36072 rx 33032


===== Docker: version / ps -a / images / networks =====
Docker version 29.4.1, build 055a478
--- docker ps -a ---
CONTAINER ID   IMAGE                              COMMAND                  CREATED       STATUS                     PORTS                          NAMES
5d97a98978e1   openclaw-sandbox:bookworm-slim     "sleep infinity"         6 days ago    Exited (137) 7 hours ago                                  openclaw-sbx-agent-main-f331f052
e5c19558cb52   n8nio/n8n:latest                   "tini -- /docker-ent…"   2 weeks ago   Exited (0) 2 weeks ago                                    n8n-claw
2f15e1db2853   node:22-bookworm                   "docker-entrypoint.s…"   2 weeks ago   Up 7 hours                 3334/tcp                       n8n-claw-minimax-media-mcp
e1bb70c57170   node:22-bookworm                   "docker-entrypoint.s…"   2 weeks ago   Up 7 hours                 3333/tcp                       n8n-claw-minimax-mcp
26a8368529fe   supabase/studio:20250113-83c9420   "docker-entrypoint.s…"   2 weeks ago   Up 7 hours (unhealthy)     127.0.0.1:3001->3000/tcp       n8n-claw-studio
9005f443fd38   kong:2.8.1                         "/docker-entrypoint.…"   2 weeks ago   Up 7 hours (healthy)       8000-8001/tcp, 8443-8444/tcp   n8n-claw-kong
7b680de0c72b   supabase/postgres-meta:v0.95.2     "docker-entrypoint.s…"   2 weeks ago   Up 7 hours (healthy)       8080/tcp                       n8n-claw-meta
dfaf4803ee69   postgrest/postgrest:v14.5          "/bin/postgrest"         2 weeks ago   Exited (1) 7 hours ago                                    n8n-claw-rest
0ea1f04919f7   supabase/postgres:15.8.1.085       "docker-entrypoint.s…"   2 weeks ago   Up 7 hours (healthy)       127.0.0.1:5432->5432/tcp       n8n-claw-db
5fd03164da5e   unclecode/crawl4ai:latest          "supervisord -c supe…"   2 weeks ago   Up 7 hours (healthy)       6379/tcp                       n8n-claw-crawl4ai
7956d8d2539e   n8n-claw-email-bridge              "docker-entrypoint.s…"   2 weeks ago   Up 7 hours                 3100/tcp                       n8n-claw-email-bridge
4357555eeb2d   searxng/searxng:latest             "/usr/local/searxng/…"   2 weeks ago   Up 7 hours                 127.0.0.1:8888->8080/tcp       n8n-claw-searxng
--- docker images ---
WARNING: This output is designed for human readability. For machine-readable output, please use --format.
IMAGE                              ID             DISK USAGE   CONTENT SIZE   EXTRA
alpine:latest                      25109184c71b       13.1MB         3.95MB
debian:bookworm-slim               4724b8cc51e3        116MB         30.6MB   U
kong:2.8.1                         1b53405d8680        203MB         49.3MB   U
n8n-claw-email-bridge:latest       ce1bce19f402        227MB         54.6MB   U
n8nio/n8n:latest                   4f448824ec99       2.01GB          283MB   U
node:22-bookworm                   7e791fc54bd0       1.64GB          424MB   U
openclaw-sandbox:bookworm-slim     4724b8cc51e3        116MB         30.6MB   U
postgrest/postgrest:v14.5          b574528fe109       27.3MB         6.29MB   U
searxng/searxng:latest             4d7ed8b7035e        382MB         97.9MB   U
supabase/postgres-meta:v0.95.2     fd819ee65489        503MB         98.8MB   U
supabase/postgres:15.8.1.085       af083ef64d04          3GB          681MB   U
supabase/studio:20250113-83c9420   29cade83d6f2        981MB          237MB   U
unclecode/crawl4ai:latest          a45fd08f8f15        5.5GB         1.47GB   U
--- docker network ls ---
NETWORK ID     NAME                    DRIVER    SCOPE
bc3a3994122b   bridge                  bridge    local
61223fcf88f0   host                    host      local
95e005d1e291   n8n-claw_n8n-claw-net   bridge    local
db492d45acdb   none                    null      local


===== Postgres (any form) =====
--- containers ---
7b680de0c72b   supabase/postgres-meta:v0.95.2     "docker-entrypoint.s…"   2 weeks ago   Up 7 hours (healthy)       8080/tcp                       n8n-claw-meta
dfaf4803ee69   postgrest/postgrest:v14.5          "/bin/postgrest"         2 weeks ago   Exited (1) 7 hours ago                                    n8n-claw-rest
0ea1f04919f7   supabase/postgres:15.8.1.085       "docker-entrypoint.s…"   2 weeks ago   Up 7 hours (healthy)       127.0.0.1:5432->5432/tcp       n8n-claw-db
--- systemd service ---
Unit postgresql.service could not be found.
--- data dir ---
ls: cannot access '/var/lib/postgresql': No such file or directory
no /var/lib/postgresql
--- sudo -n psql -l ---
sudo: unknown user postgres
sudo: error initializing audit plugin sudoers_audit


===== Python environment =====
Python 3.12.3
/usr/bin/python3
bash: line 55: pip3: command not found
pip3 not installed
--- home dir contents ---
total 276
drwxr-x--- 32 uniceadmin uniceadmin  4096 Apr 22 09:12 .
drwxr-xr-x  3 root       root        4096 Apr  3 12:38 ..
-rw-------  1 uniceadmin uniceadmin 50319 Apr 22 12:40 .bash_history
-rw-r--r--  1 uniceadmin uniceadmin   220 Apr  3 12:38 .bash_logout
-rw-r--r--  1 uniceadmin uniceadmin  4327 Apr 15 15:22 .bashrc
drwx------ 11 uniceadmin uniceadmin  4096 Apr 17 22:00 .cache
drwx------ 16 uniceadmin uniceadmin  4096 Apr 11 20:36 career-ops
drwxrwxr-x 10 uniceadmin uniceadmin  4096 Apr 19 12:50 .claude
-rw-------  1 uniceadmin uniceadmin 22513 Apr 21 20:22 .claude.json
drwxrwxr-x  2 uniceadmin uniceadmin  4096 Apr 19 12:40 .cloudflared
-rw-r--r--  1 uniceadmin uniceadmin     0 Apr  3 12:38 .cloud-locale-test.skip
drwxrwxr-x  5 uniceadmin uniceadmin  4096 Apr  9 14:49 .codex
drwxrwxr-x  7 uniceadmin uniceadmin  4096 Apr 17 22:00 .config
drwxrwxr-x  5 uniceadmin uniceadmin  4096 Apr  3 13:19 .cursor
drwxrwxr-x  5 uniceadmin uniceadmin  4096 Apr  3 12:47 .cursor-server
-rw-------  1 uniceadmin uniceadmin  1289 Apr 22 13:22 .env
-rw-rw-r--  1 uniceadmin uniceadmin    59 Apr 15 15:09 .gitconfig
drwx------  3 uniceadmin uniceadmin  4096 Apr  5 17:30 .gnupg
-rw-------  1 uniceadmin uniceadmin    20 Apr 15 15:52 .lesshst
drwxrwxr-x  6 uniceadmin docker      4096 Apr 11 23:00 .local
drwxrwxr-x  3 uniceadmin uniceadmin  4096 Apr 14 22:13 .mempalace
drwxrwxr-x  2 uniceadmin uniceadmin  4096 Apr  9 08:32 mempalace
drwxrwxr-x  4 uniceadmin uniceadmin  4096 Apr  9 09:15 mempalace_career_ops
drwxrwxr-x  2 uniceadmin uniceadmin  4096 Apr  9 08:33 mempalace_landlit
drwxrwxr-x  4 uniceadmin uniceadmin  4096 Apr  9 09:15 mempalace_ninja_clan
drwxrwxr-x  2 uniceadmin uniceadmin  4096 Apr  9 08:33 mempalace_unice
drwxrwxr-x  2 uniceadmin uniceadmin  4096 Apr  9 08:33 mempalace_whatwasdrawn
drwxr-xr-x 12 uniceadmin uniceadmin  4096 Apr  6 21:32 n8n-claw
drwxrwxr-x  8 uniceadmin uniceadmin  4096 Apr 22 14:39 ninja-butler-brain
drwxrwxr-x  2 uniceadmin uniceadmin  4096 Apr  5 11:04 ninja-clan
-rw-r--r--  1 uniceadmin uniceadmin  2884 Apr  5 18:20 ninja-clan.tail998dd.ts.net.crt
-rw-------  1 uniceadmin uniceadmin   227 Apr  5 18:20 ninja-clan.tail998dd.ts.net.key
-rw-r--r--  1 uniceadmin uniceadmin 28524 Apr  8 18:27 ninja-job-hunt.py
drwxrwxr-x  2 uniceadmin uniceadmin  4096 Apr  4 21:17 ninja-warrior
drwxrwxr-x  6 uniceadmin uniceadmin  4096 Apr 17 22:00 .npm
drwxrwxr-x  4 uniceadmin uniceadmin  4096 Apr  8 22:14 .npm-global
-rw-------  1 uniceadmin uniceadmin    36 Apr  8 22:14 .npmrc
drwxrwxr-x  8 uniceadmin uniceadmin  4096 Apr  5 11:01 .nvm
drwxr-xr-x  2 uniceadmin uniceadmin  4096 Apr 22 09:12 .ollama


===== Running services matching butler/ninja/telegram/minimax =====
(none matched)


===== User crontab =====
TZ=Australia/Sydney
# Butler cronjobs — AEST times (system local TZ = Australia/Sydney UTC+10)
# NOTE: All hour fields are AEST (UTC+10)

# Morning Briefing: 10am AEST

# Check-ins: 9am, 12pm, 3pm, 6pm, 8pm, 10pm, 11pm AEST
0 9,12,15,18,20,22,23 * * * python3 /home/uniceadmin/.openclaw/workspace/cron/checkin.py >> /home/uniceadmin/.openclaw/workspace/logs/checkins-cron.log 2>&1

# Butler Backup: 12pm AEST
0 12 * * * ~/.openclaw/workspace/cron/butler-backup.sh >> ~/.openclaw/workspace/logs/backup.log 2>&1

# Nightly Diary: 11:30pm AEST
30 23 * * * python3 ~/.openclaw/workspace/cron/create-diary.py >> ~/.openclaw/workspace/logs/diary.log 2>&1

# Medications:
# Metformin: 9pm AEST initial → pester every 15min until acknowledged (max 5)
0 21 * * * python3 ~/.openclaw/workspace/cron/medication-reminder.py metformin >> ~/.openclaw/workspace/logs/medication.log 2>&1
0,15,30,45 * * * * python3 ~/.openclaw/workspace/cron/medication-reminder.py metformin pester >> ~/.openclaw/workspace/logs/medication.log 2>&1

# Ozempic Monday: 8pm AEST initial → pester every 15min Mon until acknowledged (max 5)
0 20 * * 1 python3 ~/.openclaw/workspace/cron/medication-reminder.py ozempic >> ~/.openclaw/workspace/logs/medication.log 2>&1
0,15,30,45 * * * 1 python3 ~/.openclaw/workspace/cron/medication-reminder.py ozempic pester >> ~/.openclaw/workspace/logs/medication.log 2>&1

# Voice transcriber: auto-transcribe voice messages on receive
@reboot python3 ~/.openclaw/workspace/cron/voice-transcriber.py >> ~/.openclaw/workspace/logs/voice-transcriber-stdout.log 2>&1

# Morning Briefing: 8am AEST (via bash script with Notion Calendar DB)
0 8 * * * /home/uniceadmin/.openclaw/scripts/morning-briefing.py >> ~/.openclaw/workspace/logs/morning-briefing.log 2>&1

# Calendar sync: every 15 min (iCloud → Notion Calendar DB)
*/15 * * * * python3 /home/uniceadmin/.openclaw/scripts/sync-calendar-to-notion.py >> ~/.openclaw/logs/sync-calendar.log 2>&1

# Morning Hype Track: 8:15am AEST
15 8 * * * python3 ~/.openclaw/workspace/cron/music-maker.py hype >> ~/.openclaw/workspace/logs/music.log 2>&1

# Night Lullaby: 11:45pm AEST
45 23 * * * python3 ~/.openclaw/workspace/cron/music-maker.py lullaby >> ~/.openclaw/workspace/logs/music.log 2>&1


===== Disk: ~/ usage / docker / var/log =====
4.0K	/home/uniceadmin/ninja-clan
4.0K	/home/uniceadmin/ninja-clan.tail998dd.ts.net.crt
4.0K	/home/uniceadmin/ninja-clan.tail998dd.ts.net.key
4.0K	/home/uniceadmin/ninja-warrior
8.0K	/home/uniceadmin/mempalace
8.0K	/home/uniceadmin/mempalace_landlit
8.0K	/home/uniceadmin/mempalace_unice
8.0K	/home/uniceadmin/mempalace_whatwasdrawn
8.0K	/home/uniceadmin/scripts
28K	/home/uniceadmin/ninja-job-hunt.py
124K	/home/uniceadmin/projects
344K	/home/uniceadmin/mempalace_ninja_clan
2.9M	/home/uniceadmin/ninja-butler-brain
11M	/home/uniceadmin/career-ops
15M	/home/uniceadmin/n8n-claw
36M	/home/uniceadmin/mempalace_career_ops
--- /var/lib/docker ---
sudo: a password is required
du: cannot read directory '/var/lib/docker': Permission denied
4.0K	/var/lib/docker
[cannot read without sudo]
--- /var/log ---
sudo: a password is required
du: cannot read directory '/var/log/private': Permission denied
du: cannot read directory '/var/log/letsencrypt': Permission denied
du: cannot read directory '/var/log/unattended-upgrades': Permission denied
923M	/var/log
[cannot read without sudo]


===== Playwright prerequisites (libnss3/libatk/libxkbcommon/libasound2) =====
ii  libxkbcommon0:amd64             1.6.0-1build1                                    amd64        library interface to the XKB compiler - shared library
--- count (target >=4 for Chromium) ---
1


===== Secrets inventory (names only) =====
ls: cannot access '/home/uniceadmin/.secrets': No such file or directory
(no ~/.secrets dir)
--- ~/.env* ---
-rw------- 1 uniceadmin uniceadmin 1289 Apr 22 13:22 /home/uniceadmin/.env
--- ~/.config/ninja ---
ls: cannot access '/home/uniceadmin/.config/ninja': No such file or directory
(no ~/.config/ninja)


===== Reverse proxy / TLS: caddy / nginx =====
/usr/sbin/nginx
--- caddy status ---
Unit caddy.service could not be found.
--- nginx status ---
● nginx.service - A high performance web server and a reverse proxy server
     Loaded: loaded (/usr/lib/systemd/system/nginx.service; enabled; preset: enabled)
     Active: active (running) since Mon 2026-04-13 15:10:59 AEST; 1 week 2 days ago
       Docs: man:nginx(8)
   Main PID: 883121 (nginx)
      Tasks: 5 (limit: 9249)


===== Fail2ban status =====
Status
|- Number of jail:	4
`- Jail list:	nginx-http-auth, nginx-limit-req, recidive, sshd


===== Live memory + CPU (top -bn1 | head -15) =====
top - 17:48:46 up 16 days, 19:04,  2 users,  load average: 0.18, 0.16, 0.17
Tasks: 205 total,   1 running, 204 sleeping,   0 stopped,   0 zombie
%Cpu(s):  2.2 us,  2.2 sy,  0.0 ni, 95.7 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st
MiB Mem :   7745.7 total,   1226.7 free,   2953.0 used,   3930.7 buff/cache
MiB Swap:      0.0 total,      0.0 free,      0.0 used.   4792.7 avail Mem

    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
1002851 unicead+  20   0   11900   5756   3624 R   9.1   0.1   0:00.02 top
      1 root      20   0   22440  13852   9672 S   0.0   0.2   6:59.89 systemd
      2 root      20   0       0      0      0 S   0.0   0.0   0:00.70 kthreadd
      3 root      20   0       0      0      0 S   0.0   0.0   0:00.70 pool_wo+
      4 root       0 -20       0      0      0 I   0.0   0.0   0:00.00 kworker+
      5 root       0 -20       0      0      0 I   0.0   0.0   0:00.00 kworker+
      6 root       0 -20       0      0      0 I   0.0   0.0   0:00.00 kworker+
      7 root       0 -20       0      0      0 I   0.0   0.0   0:00.00 kworker+


===== ninja-publisher target dir state =====
--- ~/ninja-clan/ (target for Phase C2 Postiz + publisher) ---
total 8
drwxrwxr-x  2 uniceadmin uniceadmin 4096 Apr  5 11:04 .
drwxr-x--- 32 uniceadmin uniceadmin 4096 Apr 22 09:12 ..
--- ~/mempalace_ninja_clan/ (archive per memory) ---
total 56
drwxrwxr-x  4 uniceadmin uniceadmin 4096 Apr  9 09:15 .
drwxr-x--- 32 uniceadmin uniceadmin 4096 Apr 22 09:12 ..
-rw-rw-r--  1 uniceadmin uniceadmin 7874 Apr  5 11:19 AGENTS.md
drwxrwxr-x  2 uniceadmin uniceadmin 4096 Apr  9 09:18 content
drwxrwxr-x  2 uniceadmin uniceadmin 4096 Apr  9 09:18 cron
-rw-rw-r--  1 uniceadmin uniceadmin  193 Apr  5 11:19 HEARTBEAT.md
-rw-rw-r--  1 uniceadmin uniceadmin  688 Apr  8 09:29 IDENTITY.md
-rw-rw-r--  1 uniceadmin uniceadmin 5704 Apr 15 16:03 MEMORY.md
-rw-rw-r--  1 uniceadmin uniceadmin  224 Apr  9 08:33 mempalace.yaml
--- ~/.openclaw/workspace/ (active Butler scripts per memory) ---
total 268
drwxrwxr-x 12 uniceadmin uniceadmin   4096 Apr 22 16:01 .
drwx------ 25 uniceadmin uniceadmin   4096 Apr 22 16:00 ..
-rw-rw-r--  1 uniceadmin uniceadmin   7788 Apr 15 14:19 AGENTS.md
-rw-rw-r--  1 uniceadmin uniceadmin   2151 Apr 15 15:10 ARCHITECTURE.md
-rw-r--r--  1 uniceadmin uniceadmin  45244 Apr 22 12:19 attached.jpg
-rw-------  1 uniceadmin uniceadmin   4560 Apr 22 16:00 backfill-diary.py
drwxrwxr-x  3 uniceadmin uniceadmin   4096 Apr 22 14:17 cron
-rw-------  1 uniceadmin uniceadmin   1313 Apr 22 16:01 diary-standard.md
drwxrwxr-x  2 uniceadmin uniceadmin   4096 Apr 22 16:01 drawers


===== END OF AUDIT =====
Wed Apr 22 05:48:46 PM AEST 2026
````
