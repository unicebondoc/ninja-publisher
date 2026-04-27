"""Playwright-based Medium publisher.

Replaces the dead REST API approach -- Medium killed token generation.
Uses browser automation with a saved session (storage state JSON).
"""

from __future__ import annotations

import os
import time
from typing import Any

import markdown
from playwright.sync_api import sync_playwright

from base import Article, BasePublisher, PublishError, PublishResult

DEFAULT_CANONICAL_BASE = "https://unicebondoc.com/blog"
DEFAULT_SESSION_PATH = os.path.expanduser("~/.config/ninja-publisher/medium-session.json")
DEFAULT_TIMEOUT_SECONDS = 60

# Selectors -- Medium's editor changes often; we try several.
_TITLE_SELECTORS = [
    'h3[data-testid="storyTitle"]',
    "h3.graf--title",
    'div[data-testid="editorTitleParagraph"]',
    "article h3:first-of-type",
    'h3[contenteditable="true"]',
    'h4[contenteditable="true"]',
    'div[role="textbox"]:first-of-type',
]

_BODY_SELECTORS = [
    "div.ProseMirror",
    'div[role="textbox"]',
    'div[contenteditable="true"].section-content',
    "article .section-inner",
    'p[data-testid="editorParagraphParagraph"]',
    "div.section-inner p",
]

_PUBLISH_BUTTON_SELECTORS = [
    'button[data-testid="publishButton"]',
    'button:has-text("Publish")',
    'button:has-text("Ready to publish")',
]

_PUBLISH_CONFIRM_SELECTORS = [
    'button[data-testid="publishConfirmButton"]',
    'button:has-text("Publish now")',
    'button:has-text("Publish"):visible >> nth=-1',
]

_TAG_INPUT_SELECTORS = [
    'input[placeholder*="tag"]',
    'input[data-testid="tagInput"]',
    'input[aria-label*="tag" i]',
]


class MediumPublisher(BasePublisher):
    """Publish articles to Medium via Playwright browser automation."""

    platform = "medium"

    def __init__(
        self,
        session_path: str | None = None,
        canonical_base: str | None = None,
        dry_run: bool = False,
        timeout_seconds: int | None = None,
    ) -> None:
        self.session_path = (
            session_path or os.environ.get("MEDIUM_SESSION_PATH") or DEFAULT_SESSION_PATH
        )
        self.canonical_base = (
            canonical_base or os.environ.get("CANONICAL_BASE_URL") or DEFAULT_CANONICAL_BASE
        ).rstrip("/")
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds or int(
            os.environ.get("MEDIUM_PUBLISH_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        )

    def canonical_url_for(self, article: Article) -> str:
        return article.canonical_url or f"{self.canonical_base}/{article.slug}"

    def publish(self, article: Article, images: list[bytes] | None = None) -> PublishResult:  # noqa: ARG002 — images unused for now
        """Publish an article to Medium using browser automation."""
        self._validate_session()

        timeout_ms = self.timeout_seconds * 1000
        browser = None
        page = None

        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(storage_state=self.session_path)
            context.set_default_timeout(timeout_ms)
            page = context.new_page()

            # Navigate to editor
            page.goto("https://medium.com/new-story", wait_until="networkidle")

            # Detect login redirect
            if "/m/signin" in page.url:
                raise PublishError(
                    self.platform,
                    "Medium session expired. Run scripts/medium-login.sh to re-authenticate.",
                    status=401,
                    raw={"url": page.url},
                )

            # Type title
            self._type_title(page, article.title)

            # Move to body and insert content
            page.keyboard.press("Tab")
            self._insert_body(page, article.body_markdown)

            if self.dry_run:
                return self._handle_dry_run(page, browser, pw)

            # Click publish button (opens dialog)
            self._click_first_match(page, _PUBLISH_BUTTON_SELECTORS, "publish button")

            # Add tags in the publish dialog (max 5)
            self._add_tags(page, article.tags[:5])

            # Click final publish confirm
            self._click_first_match(page, _PUBLISH_CONFIRM_SELECTORS, "publish confirm button")

            # Wait for navigation to published article
            page.wait_for_url("**/p/**", timeout=timeout_ms)
            published_url = page.url

            browser.close()
            pw.stop()

            return PublishResult(
                platform=self.platform,
                url=published_url,
                id=None,
                raw={},
            )

        except PublishError:
            raise
        except Exception as exc:
            self._screenshot_on_failure(page)
            raise PublishError(
                self.platform,
                f"Playwright publish failed: {exc}",
                raw={"error": str(exc)},
            ) from exc
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
            try:
                if pw:  # noqa: F821 — pw may not be bound if sync_playwright() fails
                    pw.stop()  # type: ignore[possibly-undefined]
            except Exception:  # noqa: BLE001
                pass

    def _validate_session(self) -> None:
        """Check that the session file exists and is readable."""
        if not os.path.isfile(self.session_path):
            raise PublishError(
                self.platform,
                f"Session file not found at {self.session_path}. "
                "Run scripts/medium-login.sh to authenticate.",
                status=0,
                raw={
                    "error": f"Session file not found at {self.session_path}. "
                    "Run scripts/medium-login.sh to authenticate."
                },
            )

    def _type_title(self, page: Any, title: str) -> None:
        """Find the title element and type into it."""
        for selector in _TITLE_SELECTORS:
            try:
                el = page.query_selector(selector)
                if el:
                    el.click()
                    page.keyboard.type(title, delay=10)
                    return
            except Exception:  # noqa: BLE001 — try next selector
                continue
        # Fallback: click first contenteditable and type
        page.click('[contenteditable="true"]', timeout=5000)
        page.keyboard.type(title, delay=10)

    def _insert_body(self, page: Any, body_markdown: str) -> None:
        """Convert markdown to HTML and insert into the editor body."""
        html = markdown.markdown(body_markdown, extensions=["extra", "codehilite"])
        # Try to find the body editor and set content via clipboard paste
        # approach, which triggers Medium's internal state better than innerHTML.
        inserted = page.evaluate(
            """(html) => {
            const selectors = [
                'div.ProseMirror',
                'div[role="textbox"]',
                'div[contenteditable="true"]',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) {
                    el.innerHTML = html;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }""",
            html,
        )
        if not inserted:
            # Fallback: just type the raw markdown
            page.keyboard.type(body_markdown)

    def _add_tags(self, page: Any, tags: list[str]) -> None:
        """Type tags into the publish dialog's tag input."""
        for selector in _TAG_INPUT_SELECTORS:
            try:
                el = page.query_selector(selector)
                if el:
                    for tag in tags:
                        el.click()
                        page.keyboard.type(tag, delay=10)
                        page.keyboard.press("Enter")
                    return
            except Exception:  # noqa: BLE001 — tags are best-effort
                continue

    def _click_first_match(self, page: Any, selectors: list[str], description: str) -> None:
        """Click the first matching selector from a list."""
        for selector in selectors:
            try:
                el = page.query_selector(selector)
                if el:
                    el.click()
                    return
            except Exception:  # noqa: BLE001
                continue
        raise PublishError(
            self.platform,
            f"Could not find {description}. Medium UI may have changed.",
            raw={"selectors_tried": selectors},
        )

    def _handle_dry_run(self, page: Any, browser: Any, pw: Any) -> PublishResult:
        """Take screenshot and return synthetic result for dry run."""
        ts = int(time.time())
        screenshot_path = f"/tmp/ninja-publisher-medium-dry-run-{ts}.png"
        page.screenshot(path=screenshot_path)
        browser.close()
        pw.stop()
        return PublishResult(
            platform=self.platform,
            url="https://medium.com/dry-run",
            id="dry-run",
            raw={"screenshot": screenshot_path},
        )

    @staticmethod
    def _screenshot_on_failure(page: Any) -> None:
        """Best-effort screenshot capture on failure."""
        if page is None:
            return
        try:
            ts = int(time.time())
            page.screenshot(path=f"/tmp/ninja-publisher-medium-fail-{ts}.png")
        except Exception:  # noqa: BLE001 — best-effort
            pass
