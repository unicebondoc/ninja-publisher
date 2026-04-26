#!/usr/bin/env bash
# verify-phase-e.sh — Verification checks for Phase E deployment
# Runs as uniceadmin (no sudo). Non-destructive, read-only checks.
set -uo pipefail

REPO_DIR="/home/uniceadmin/ninja-clan/agent-teams/worktrees/ninja-publisher"
VENV_DIR="${REPO_DIR}/.venv"
ENV_FILE="${REPO_DIR}/.env"
TUNNEL_CFG="${HOME}/.cloudflared/config.yml"

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

pass() { echo "  PASS: $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { echo "  FAIL: $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
skip() { echo "  SKIP: $1"; SKIP_COUNT=$((SKIP_COUNT + 1)); }

echo "=== Phase E Verification ==="
echo ""

# ── 1. Venv exists ──────────────────────────────────────────────────────────
echo "--- 1. Venv exists ---"
if "${VENV_DIR}/bin/python" --version > /dev/null 2>&1; then
    pass "Venv python: $("${VENV_DIR}/bin/python" --version)"
else
    fail "Venv python not found at ${VENV_DIR}/bin/python"
fi

# ── 2. Venv imports ─────────────────────────────────────────────────────────
echo "--- 2. Venv imports ---"
if "${VENV_DIR}/bin/python" -c "import flask, slack_sdk, notion_client, slugify, requests, dotenv" 2>/dev/null; then
    pass "All required packages importable"
else
    fail "One or more required packages not importable"
fi

# ── 3. systemd active ───────────────────────────────────────────────────────
echo "--- 3. systemd service ---"
STATUS=$(systemctl --user is-active ninja-publisher 2>/dev/null || true)
if [ "${STATUS}" = "active" ]; then
    pass "ninja-publisher service is active"
else
    fail "ninja-publisher service is ${STATUS:-unknown}"
fi

# ── 4. Health (local) ───────────────────────────────────────────────────────
echo "--- 4. Health (local) ---"
if curl -sf http://localhost:8080/health 2>/dev/null | grep -q '"ok"'; then
    pass "Local health check returns ok"
else
    fail "Local health check failed"
fi

# ── 5. .env exists + permissions ─────────────────────────────────────────────
echo "--- 5. .env file ---"
if [ -f "${ENV_FILE}" ]; then
    PERMS=$(stat -c %a "${ENV_FILE}" 2>/dev/null || echo "unknown")
    if [ "${PERMS}" = "600" ]; then
        pass ".env exists with permissions 600"
    else
        fail ".env permissions are ${PERMS} (expected 600)"
    fi
else
    fail ".env does not exist"
fi

# ── 6. .env format ──────────────────────────────────────────────────────────
echo "--- 6. .env format ---"
if [ -f "${ENV_FILE}" ]; then
    FORMAT_ISSUES=0
    while IFS= read -r line; do
        [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
        if [[ "${line}" =~ ^export[[:space:]] ]]; then
            FORMAT_ISSUES=$((FORMAT_ISSUES + 1))
        fi
        if [[ "${line}" =~ ^[A-Za-z_]+=\".*\" ]]; then
            FORMAT_ISSUES=$((FORMAT_ISSUES + 1))
        fi
        if [[ "${line}" =~ ^[A-Za-z_]+=.*[[:space:]]#.* ]]; then
            FORMAT_ISSUES=$((FORMAT_ISSUES + 1))
        fi
        key_part="${line%%=*}"
        if [[ "${key_part}" =~ [[:space:]] ]]; then
            FORMAT_ISSUES=$((FORMAT_ISSUES + 1))
        fi
    done < "${ENV_FILE}"

    if [ "${FORMAT_ISSUES}" -eq 0 ]; then
        pass ".env format is systemd-compatible"
    else
        fail ".env has ${FORMAT_ISSUES} format issue(s)"
    fi
else
    skip ".env format check (file missing)"
fi

# ── 7. Required env vars non-empty ──────────────────────────────────────────
echo "--- 7. Required env vars ---"
if [ -f "${ENV_FILE}" ]; then
    REQUIRED_KEYS=(MEDIUM_TOKEN MINIMAX_API_KEY NOTION_TOKEN SLACK_BOT_TOKEN SLACK_SIGNING_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID)
    EMPTY_KEYS=0
    for key in "${REQUIRED_KEYS[@]}"; do
        val=$(grep "^${key}=" "${ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-)
        if [ -z "${val}" ]; then
            EMPTY_KEYS=$((EMPTY_KEYS + 1))
        fi
    done
    if [ "${EMPTY_KEYS}" -eq 0 ]; then
        pass "All ${#REQUIRED_KEYS[@]} required secrets are populated"
    else
        fail "${EMPTY_KEYS} required secret(s) are empty"
    fi
else
    skip "Required env vars check (file missing)"
fi

# ── 8. Tunnel validates ─────────────────────────────────────────────────────
echo "--- 8. Tunnel config ---"
if command -v cloudflared > /dev/null 2>&1 && [ -f "${TUNNEL_CFG}" ]; then
    if cloudflared tunnel ingress validate 2>&1 | grep -qi "ok\|valid"; then
        pass "Tunnel ingress validates"
    else
        # cloudflared may exit 0 with no output on success
        if cloudflared tunnel ingress validate > /dev/null 2>&1; then
            pass "Tunnel ingress validates"
        else
            fail "Tunnel ingress validation failed"
        fi
    fi
else
    skip "Tunnel validation (cloudflared or config not found)"
fi

# ── 9. DNS resolves ─────────────────────────────────────────────────────────
echo "--- 9. DNS resolution ---"
if command -v dig > /dev/null 2>&1; then
    DNS_RESULT=$(dig +short publisher.unicebondoc.com 2>/dev/null)
    if [ -n "${DNS_RESULT}" ]; then
        pass "publisher.unicebondoc.com resolves: ${DNS_RESULT}"
    else
        fail "publisher.unicebondoc.com does not resolve"
    fi
else
    skip "DNS check (dig not available)"
fi

# ── 10. Health (public) ─────────────────────────────────────────────────────
echo "--- 10. Health (public) ---"
if curl -sf --max-time 10 https://publisher.unicebondoc.com/health 2>/dev/null | grep -q '"ok"'; then
    pass "Public health check returns ok"
else
    fail "Public health check failed (https://publisher.unicebondoc.com/health)"
fi

# ── 11. No secrets in logs ──────────────────────────────────────────────────
echo "--- 11. Secrets in logs ---"
LOG_OUTPUT=$(journalctl --user -u ninja-publisher --no-pager -n 50 2>/dev/null || echo "")
if [ -n "${LOG_OUTPUT}" ]; then
    if echo "${LOG_OUTPUT}" | grep -qiE '(Bearer\s+\S|xoxb-|ntn_|sk-)'; then
        fail "Potential secrets found in recent logs"
    else
        pass "No secrets detected in recent logs"
    fi
else
    skip "Log secrets check (no journal output)"
fi

# ── 12. Notion connectivity ─────────────────────────────────────────────────
echo "--- 12. Notion connectivity ---"
if [ -f "${ENV_FILE}" ]; then
    NOTION_RESULT=$("${VENV_DIR}/bin/python" -c "
import os
from dotenv import load_dotenv
load_dotenv('${ENV_FILE}', override=True)
from notion_client import Client
try:
    c = Client(auth=os.environ.get('NOTION_TOKEN', ''))
    db = c.databases.retrieve(os.environ.get('NOTION_DB_ID', ''))
    print('ok:' + db.get('title', [{}])[0].get('plain_text', 'untitled'))
except Exception as e:
    print('err:' + str(e)[:80])
" 2>/dev/null || echo "err:python failed")

    if [[ "${NOTION_RESULT}" == ok:* ]]; then
        pass "Notion DB accessible: ${NOTION_RESULT#ok:}"
    else
        fail "Notion connectivity: ${NOTION_RESULT#err:}"
    fi
else
    skip "Notion connectivity (no .env)"
fi

# ── 13. Telegram connectivity ───────────────────────────────────────────────
echo "--- 13. Telegram connectivity ---"
if [ -f "${ENV_FILE}" ]; then
    TG_RESULT=$("${VENV_DIR}/bin/python" -c "
import os, requests
from dotenv import load_dotenv
load_dotenv('${ENV_FILE}', override=True)
token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
if not token:
    print('err:no token')
else:
    try:
        r = requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=10)
        data = r.json()
        if data.get('ok'):
            print('ok:' + data['result'].get('username', 'unknown'))
        else:
            print('err:' + data.get('description', 'unknown error')[:80])
    except Exception as e:
        print('err:' + str(e)[:80])
" 2>/dev/null || echo "err:python failed")

    if [[ "${TG_RESULT}" == ok:* ]]; then
        pass "Telegram bot reachable: @${TG_RESULT#ok:}"
    else
        fail "Telegram connectivity: ${TG_RESULT#err:}"
    fi
else
    skip "Telegram connectivity (no .env)"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "Phase E Verification Summary"
echo "========================================"
echo "  PASS: ${PASS_COUNT}"
echo "  FAIL: ${FAIL_COUNT}"
echo "  SKIP: ${SKIP_COUNT}"
echo "========================================"

if [ "${FAIL_COUNT}" -gt 0 ]; then
    echo "Some checks failed. Review output above."
    exit 1
else
    echo "All checks passed!"
    exit 0
fi
