#!/usr/bin/env bash
# deploy-phase-e.sh — Idempotent deployment of ninja-publisher to Butler VPS
# Runs as uniceadmin (no sudo). Safe to re-run.
set -euo pipefail

REPO_DIR="/home/uniceadmin/ninja-clan/agent-teams/worktrees/ninja-publisher"
VENV_DIR="${REPO_DIR}/.venv"
UNIT_DIR="${HOME}/.config/systemd/user"
TUNNEL_CFG="${HOME}/.cloudflared/config.yml"

echo "=== Phase E Deploy: ninja-publisher ==="
echo "Repo:   ${REPO_DIR}"
echo "Venv:   ${VENV_DIR}"
echo ""

# ── 1. Venv bootstrap ───────────────────────────────────────────────────────
echo "--- Venv bootstrap ---"
if [ ! -f "${VENV_DIR}/bin/python" ]; then
    echo "Creating venv..."
    python3 -m venv "${VENV_DIR}"
fi
echo "Installing requirements..."
"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/requirements.txt" --quiet
echo "Verifying imports..."
"${VENV_DIR}/bin/python" -c "import flask, slack_sdk, notion_client, slugify, requests, dotenv"
echo "Venv OK"
echo ""

# ── 2. .env checks ──────────────────────────────────────────────────────────
echo "--- .env validation ---"
ENV_FILE="${REPO_DIR}/.env"
if [ ! -f "${ENV_FILE}" ]; then
    echo "ERROR: ${ENV_FILE} does not exist."
    echo "Create it from .env.template:"
    echo "  cp ${REPO_DIR}/.env.template ${ENV_FILE}"
    echo "  # Fill in real values, then re-run this script."
    exit 1
fi

chmod 600 "${ENV_FILE}"
echo ".env permissions set to 600"

# Validate .env format (warn on common mistakes)
ENV_WARNINGS=0
while IFS= read -r line; do
    # Skip empty lines and comments
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue

    if [[ "${line}" =~ ^export[[:space:]] ]]; then
        echo "WARNING: 'export' prefix found: ${line}"
        ENV_WARNINGS=$((ENV_WARNINGS + 1))
    fi
    if [[ "${line}" =~ ^[A-Za-z_]+=\".*\" ]]; then
        echo "WARNING: quoted value found: ${line}"
        ENV_WARNINGS=$((ENV_WARNINGS + 1))
    fi
    if [[ "${line}" =~ ^[A-Za-z_]+=.*[[:space:]]#.* ]]; then
        echo "WARNING: inline comment found: ${line}"
        ENV_WARNINGS=$((ENV_WARNINGS + 1))
    fi
    if [[ "${line}" =~ ^[A-Za-z_]+[[:space:]]+= || "${line}" =~ =[[:space:]]+ ]]; then
        # Check for spaces around = but not in values
        key_part="${line%%=*}"
        if [[ "${key_part}" =~ [[:space:]] ]]; then
            echo "WARNING: space before '=' found: ${line}"
            ENV_WARNINGS=$((ENV_WARNINGS + 1))
        fi
    fi
done < "${ENV_FILE}"

if [ "${ENV_WARNINGS}" -gt 0 ]; then
    echo "WARNING: ${ENV_WARNINGS} format issue(s) found. systemd EnvironmentFile may not parse correctly."
    echo "See .env.template for format rules."
fi

# Check required secrets are non-empty
REQUIRED_KEYS=(MEDIUM_TOKEN MINIMAX_API_KEY NOTION_TOKEN SLACK_BOT_TOKEN SLACK_SIGNING_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID)
MISSING=0
for key in "${REQUIRED_KEYS[@]}"; do
    val=$(grep "^${key}=" "${ENV_FILE}" | head -1 | cut -d= -f2-)
    if [ -z "${val}" ]; then
        echo "ERROR: ${key} is empty in .env"
        MISSING=$((MISSING + 1))
    fi
done

if [ "${MISSING}" -gt 0 ]; then
    echo "ERROR: ${MISSING} required secret(s) missing. Populate .env and re-run."
    exit 1
fi
echo ".env OK"
echo ""

# ── 3. systemd unit ─────────────────────────────────────────────────────────
echo "--- systemd unit ---"
mkdir -p "${UNIT_DIR}"
cp "${REPO_DIR}/scripts/ninja-publisher.service" "${UNIT_DIR}/"
echo "Unit file copied to ${UNIT_DIR}/"
echo ""

# ── 4. Cloudflare tunnel ingress ─────────────────────────────────────────────
echo "--- Cloudflare tunnel ---"
TUNNEL_CHANGED=0
if [ -f "${TUNNEL_CFG}" ]; then
    if grep -q "publisher.unicebondoc.com" "${TUNNEL_CFG}"; then
        echo "Tunnel ingress for publisher.unicebondoc.com already configured."
    else
        echo "Adding publisher.unicebondoc.com to tunnel config..."
        cp "${TUNNEL_CFG}" "${TUNNEL_CFG}.bak.phase-e"

        # Insert before the catch-all (last ingress entry: "- service: http_status:404")
        # We use python for safe YAML manipulation
        "${VENV_DIR}/bin/python" -c "
import yaml, sys

with open('${TUNNEL_CFG}', 'r') as f:
    cfg = yaml.safe_load(f)

if 'ingress' not in cfg:
    print('ERROR: no ingress key in tunnel config', file=sys.stderr)
    sys.exit(1)

ingress = cfg['ingress']
new_entry = {'hostname': 'publisher.unicebondoc.com', 'service': 'http://localhost:8080'}

# Insert before the last entry (catch-all)
ingress.insert(len(ingress) - 1, new_entry)

with open('${TUNNEL_CFG}', 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
"
        # Validate
        if cloudflared tunnel ingress validate 2>&1; then
            echo "Tunnel config validated."
            TUNNEL_CHANGED=1
        else
            echo "ERROR: Tunnel validation failed. Reverting config."
            cp "${TUNNEL_CFG}.bak.phase-e" "${TUNNEL_CFG}"
            exit 1
        fi
    fi
else
    echo "WARNING: ${TUNNEL_CFG} not found. Skipping tunnel configuration."
    echo "You will need to manually configure the Cloudflare tunnel."
fi
echo ""

# ── 5. Start services ───────────────────────────────────────────────────────
echo "--- Starting services ---"
systemctl --user daemon-reload
systemctl --user enable ninja-publisher
systemctl --user restart ninja-publisher
echo "ninja-publisher service restarted."

if [ "${TUNNEL_CHANGED}" -eq 1 ]; then
    echo "Tunnel config changed — restarting cloudflared..."
    systemctl --user restart cloudflared
fi
echo ""

# ── 6. Smoke test ────────────────────────────────────────────────────────────
echo "--- Smoke test ---"
echo "Waiting 3s for server to start..."
sleep 3

if curl -sf http://localhost:8080/health | grep -q '"ok"'; then
    echo "Health check PASSED"
else
    echo "Health check FAILED — check logs: journalctl --user -u ninja-publisher -n 50"
    exit 1
fi
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
echo "========================================"
echo "Phase E deployment complete!"
echo "========================================"
echo ""
echo "Service:  systemctl --user status ninja-publisher"
echo "Logs:     journalctl --user -u ninja-publisher -f"
echo "Health:   curl http://localhost:8080/health"
echo "Public:   https://publisher.unicebondoc.com/health"
echo ""
echo "Next: run scripts/verify-phase-e.sh for full verification."
