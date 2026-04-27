#!/usr/bin/env bash
# Medium login via remote debugging — for headless VPS
#
# Usage:
#   1. SSH to VPS with tunnel:  ssh -L 9222:127.0.0.1:9222 uniceadmin@ninja-clan
#   2. Run this script on VPS:  bash scripts/medium-login-remote.sh
#   3. On Mac, open Chrome:     chrome://inspect/#devices
#   4. Click "inspect" on the medium.com target
#   5. Log into Medium in the DevTools window
#   6. Press Enter in this terminal when logged in
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CHROME="$HOME/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
USER_DATA="/tmp/medium-login-profile"
SESSION_PATH="${1:-$HOME/.config/ninja-publisher/medium-session.json}"

rm -rf "$USER_DATA"
mkdir -p "$USER_DATA"

echo "Starting Chromium with remote debugging on port 9222..."
"$CHROME" \
    --remote-debugging-port=9222 \
    --remote-debugging-address=127.0.0.1 \
    --no-sandbox \
    --disable-gpu \
    --disable-blink-features=AutomationControlled \
    --user-data-dir="$USER_DATA" \
    --no-first-run \
    --disable-extensions \
    "https://medium.com" &

CHROME_PID=$!
sleep 3

echo ""
echo "=== Chromium is running ==="
echo "On your Mac:"
echo "  1. Open Chrome -> chrome://inspect/#devices"
echo "  2. You should see 'medium.com' under Remote Target"
echo "  3. Click 'inspect' to open DevTools with the page"
echo "  4. Log into Medium in that window"
echo "  5. Come back here and press Enter"
echo ""
read -r -p "Press Enter after you've logged in to Medium..."

# Now use Playwright to capture the session from the running browser
echo "Capturing session (saving cookies as-is)..."
"$REPO_DIR/.venv/bin/python" -c "
import json, os
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('http://127.0.0.1:9222')
    contexts = browser.contexts
    if not contexts:
        print('ERROR: No browser contexts found')
        exit(1)
    context = contexts[0]

    # Show current page URL for debugging
    pages = context.pages
    for pg in pages:
        print(f'  Page: {pg.url}')

    # Save session without navigating away
    session_path = '$SESSION_PATH'
    os.makedirs(os.path.dirname(session_path), exist_ok=True)
    state = context.storage_state()
    cookie_count = len(state.get('cookies', []))
    with open(session_path, 'w') as f:
        json.dump(state, f)
    os.chmod(session_path, 0o600)
    print(f'Session saved to {session_path} ({cookie_count} cookies)')
    browser.close()
"

# Kill Chrome
kill "$CHROME_PID" 2>/dev/null || true
wait "$CHROME_PID" 2>/dev/null || true
rm -rf "$USER_DATA"

# Verify session
echo "Verifying session..."
"$REPO_DIR/.venv/bin/python" -c "
from publishers.medium_login import verify_session
ok = verify_session('$SESSION_PATH')
exit(0 if ok else 1)
"

echo "Done!"
