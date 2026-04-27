"""Interactive login helper for Medium session management.

Two modes:
  1. Local interactive: opens browser for manual login, saves session.
  2. Remote debug: launches Chromium with remote debugging port for headless VPS.

Usage:
  python -m publishers.medium_login                     # interactive login
  python -m publishers.medium_login --verify-only       # check existing session
  python -m publishers.medium_login --remote-debug      # VPS remote debug mode
"""

from __future__ import annotations

import argparse
import os
import sys

from playwright.sync_api import sync_playwright

from publishers.medium import DEFAULT_SESSION_PATH


def login_interactive(session_path: str, headless: bool = False) -> bool:
    """Open browser for manual Medium login. User logs in, we save session."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://medium.com")

        if not headless:
            print("Log into Medium in the browser window.")  # noqa: T201
            print("Press Enter here when done...")  # noqa: T201
            input()

        # Verify login succeeded
        page.goto("https://medium.com/new-story")
        page.wait_for_load_state("networkidle")
        if "/m/signin" in page.url:
            print("ERROR: Still not logged in. Try again.")  # noqa: T201
            browser.close()
            return False

        # Save session
        os.makedirs(os.path.dirname(session_path), exist_ok=True)
        context.storage_state(path=session_path)
        os.chmod(session_path, 0o600)
        browser.close()

        print(f"Session saved to {session_path}")  # noqa: T201

        # Verify session works headlessly
        return verify_session(session_path)


def verify_session(session_path: str) -> bool:
    """Verify saved session can access Medium editor."""
    if not os.path.isfile(session_path):
        print(f"Session file not found: {session_path}")  # noqa: T201
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=session_path)
        page = context.new_page()
        page.goto("https://medium.com/new-story", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)  # let redirects settle
        ok = "/m/signin" not in page.url
        browser.close()
        if ok:
            print("Session verification: PASS (editor accessible)")  # noqa: T201
        else:
            print("Session verification: FAIL (redirected to login)")  # noqa: T201
        return ok


def login_remote_debug(session_path: str, debug_port: int = 9222) -> bool:
    """Launch Chromium with remote debugging for login from another machine."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                f"--remote-debugging-port={debug_port}",
                "--remote-debugging-address=127.0.0.1",
            ],
        )
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://medium.com", wait_until="domcontentloaded", timeout=60000)

        print(f"Remote debugging available at http://localhost:{debug_port}")  # noqa: T201
        print("Connect from your local machine via SSH tunnel:")  # noqa: T201
        print(f"  ssh -L {debug_port}:localhost:{debug_port} <vps>")  # noqa: T201
        print("Then open chrome://inspect in your browser.")  # noqa: T201
        print("Log into Medium, then press Enter here...")  # noqa: T201
        input()

        # Verify and save
        page.goto("https://medium.com/new-story")
        page.wait_for_load_state("networkidle")
        if "/m/signin" in page.url:
            print("ERROR: Still not logged in. Try again.")  # noqa: T201
            browser.close()
            return False

        os.makedirs(os.path.dirname(session_path), exist_ok=True)
        context.storage_state(path=session_path)
        os.chmod(session_path, 0o600)
        browser.close()

        print(f"Session saved to {session_path}")  # noqa: T201
        return verify_session(session_path)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Medium session login helper for ninja-publisher")
    parser.add_argument(
        "--session-path",
        default=os.environ.get("MEDIUM_SESSION_PATH", DEFAULT_SESSION_PATH),
        help=f"Path to session state JSON (default: {DEFAULT_SESSION_PATH})",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Just verify existing session (no login)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run verification in headless mode",
    )
    parser.add_argument(
        "--remote-debug",
        action="store_true",
        help="Launch with remote debugging port for VPS login",
    )
    parser.add_argument(
        "--debug-port",
        type=int,
        default=9222,
        help="Remote debugging port (default: 9222)",
    )

    args = parser.parse_args()

    if args.verify_only:
        ok = verify_session(args.session_path)
        sys.exit(0 if ok else 1)

    if args.remote_debug:
        ok = login_remote_debug(args.session_path, args.debug_port)
        sys.exit(0 if ok else 1)

    ok = login_interactive(args.session_path, headless=args.headless)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
