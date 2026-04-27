"""Tests for the Playwright-based MediumPublisher.

All Playwright calls are mocked -- no real browser needed.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from base import Article, PublishError
from publishers.medium import MediumPublisher


def _article(**overrides) -> Article:
    base = dict(
        title="AI Butler on Hetzner",
        slug="ai-butler-on-hetzner",
        body_markdown="# hello\n\nbody",
        tags=["ai", "devops"],
    )
    base.update(overrides)
    return Article(**base)


def _make_session_file(tmp_path) -> str:
    """Create a minimal valid session file."""
    session_path = os.path.join(str(tmp_path), "session.json")
    with open(session_path, "w") as f:
        json.dump({"cookies": [], "origins": []}, f)
    return session_path


def _build_mock_chain():
    """Build the Playwright mock chain: pw -> browser -> context -> page."""
    mock_page = MagicMock()
    mock_page.url = "https://medium.com/new-story"
    mock_page.query_selector.return_value = MagicMock()
    mock_page.evaluate.return_value = True

    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context

    mock_pw_instance = MagicMock()
    mock_pw_instance.chromium.launch.return_value = mock_browser

    mock_pw = MagicMock()
    mock_pw.start.return_value = mock_pw_instance

    return mock_pw, mock_pw_instance, mock_browser, mock_context, mock_page


class TestConstructor:
    def test_constructor_defaults(self, monkeypatch):
        monkeypatch.delenv("MEDIUM_SESSION_PATH", raising=False)
        monkeypatch.delenv("CANONICAL_BASE_URL", raising=False)
        monkeypatch.delenv("MEDIUM_PUBLISH_TIMEOUT_SECONDS", raising=False)

        pub = MediumPublisher()
        assert pub.session_path.endswith("medium-session.json")
        assert pub.canonical_base == "https://unicebondoc.com/blog"
        assert pub.dry_run is False
        assert pub.timeout_seconds == 60
        assert pub.platform == "medium"

    def test_constructor_env_vars(self, monkeypatch):
        monkeypatch.setenv("MEDIUM_SESSION_PATH", "/custom/session.json")
        monkeypatch.setenv("CANONICAL_BASE_URL", "https://example.com/posts")
        monkeypatch.setenv("MEDIUM_PUBLISH_TIMEOUT_SECONDS", "120")

        pub = MediumPublisher()
        assert pub.session_path == "/custom/session.json"
        assert pub.canonical_base == "https://example.com/posts"
        assert pub.timeout_seconds == 120

    def test_constructor_explicit_params(self):
        pub = MediumPublisher(
            session_path="/my/path.json",
            canonical_base="https://blog.example.com/",
            dry_run=True,
            timeout_seconds=30,
        )
        assert pub.session_path == "/my/path.json"
        assert pub.canonical_base == "https://blog.example.com"  # trailing slash stripped
        assert pub.dry_run is True
        assert pub.timeout_seconds == 30


class TestCanonicalUrl:
    def test_canonical_url_default_base(self):
        pub = MediumPublisher()
        assert (
            pub.canonical_url_for(_article()) == "https://unicebondoc.com/blog/ai-butler-on-hetzner"
        )

    def test_canonical_url_overridden_base(self):
        pub = MediumPublisher(canonical_base="https://example.com/posts/")
        assert pub.canonical_url_for(_article()) == "https://example.com/posts/ai-butler-on-hetzner"

    def test_canonical_url_env_wins(self, monkeypatch):
        monkeypatch.setenv("CANONICAL_BASE_URL", "https://env.example.com/p")
        pub = MediumPublisher()
        assert pub.canonical_url_for(_article()) == "https://env.example.com/p/ai-butler-on-hetzner"

    def test_article_can_override_canonical_url(self):
        pub = MediumPublisher()
        art = _article(canonical_url="https://custom.example/one")
        assert pub.canonical_url_for(art) == "https://custom.example/one"


class TestPublishErrors:
    def test_publish_missing_session_file(self):
        pub = MediumPublisher(session_path="/nonexistent/session.json")
        with pytest.raises(PublishError, match="Session file not found"):
            pub.publish(_article(), images=[])

    @patch("publishers.medium.sync_playwright")
    def test_publish_session_expired(self, mock_sync_pw, tmp_path):
        session_path = _make_session_file(tmp_path)
        mock_pw, mock_pw_inst, mock_browser, mock_ctx, mock_page = _build_mock_chain()
        mock_sync_pw.return_value = mock_pw

        # After navigation, URL contains signin
        mock_page.url = "https://medium.com/m/signin"

        pub = MediumPublisher(session_path=session_path)
        with pytest.raises(PublishError, match="session expired"):
            pub.publish(_article(), images=[])

    @patch("publishers.medium.sync_playwright")
    def test_publish_timeout(self, mock_sync_pw, tmp_path):
        session_path = _make_session_file(tmp_path)
        mock_pw, mock_pw_inst, mock_browser, mock_ctx, mock_page = _build_mock_chain()
        mock_sync_pw.return_value = mock_pw

        # goto raises a timeout error
        mock_page.goto.side_effect = TimeoutError("Navigation timeout")

        pub = MediumPublisher(session_path=session_path, timeout_seconds=1)
        with pytest.raises(PublishError, match="Playwright publish failed"):
            pub.publish(_article(), images=[])

    @patch("publishers.medium.sync_playwright")
    def test_publish_screenshot_on_failure(self, mock_sync_pw, tmp_path):
        session_path = _make_session_file(tmp_path)
        mock_pw, mock_pw_inst, mock_browser, mock_ctx, mock_page = _build_mock_chain()
        mock_sync_pw.return_value = mock_pw

        # Simulate failure after page loads (during title typing)
        mock_page.query_selector.return_value = None
        mock_page.click.side_effect = Exception("Element not found")

        pub = MediumPublisher(session_path=session_path)
        with pytest.raises(PublishError):
            pub.publish(_article(), images=[])

        # Screenshot should have been attempted
        mock_page.screenshot.assert_called_once()


class TestPublishHappyPath:
    @patch("publishers.medium.sync_playwright")
    def test_publish_happy_path(self, mock_sync_pw, tmp_path):
        session_path = _make_session_file(tmp_path)
        mock_pw, mock_pw_inst, mock_browser, mock_ctx, mock_page = _build_mock_chain()
        mock_sync_pw.return_value = mock_pw

        # After clicking publish, page navigates to published URL
        published_url = "https://medium.com/@unice/ai-butler-on-hetzner-abc123"

        def set_published_url(*args, **kwargs):
            mock_page.url = published_url

        mock_page.wait_for_url.side_effect = set_published_url

        pub = MediumPublisher(session_path=session_path)
        result = pub.publish(_article(), images=[])

        assert result.platform == "medium"
        assert result.url == published_url
        assert result.id is None

        # Verify correct sequence
        mock_pw_inst.chromium.launch.assert_called_once_with(headless=True)
        mock_browser.new_context.assert_called_once_with(storage_state=session_path)
        mock_page.goto.assert_called_with("https://medium.com/new-story", wait_until="networkidle")
        mock_page.keyboard.press.assert_any_call("Tab")

    @patch("publishers.medium.sync_playwright")
    def test_publish_dry_run(self, mock_sync_pw, tmp_path):
        session_path = _make_session_file(tmp_path)
        mock_pw, mock_pw_inst, mock_browser, mock_ctx, mock_page = _build_mock_chain()
        mock_sync_pw.return_value = mock_pw

        pub = MediumPublisher(session_path=session_path, dry_run=True)
        result = pub.publish(_article(), images=[])

        assert result.platform == "medium"
        assert result.url == "https://medium.com/dry-run"
        assert result.id == "dry-run"
        assert "screenshot" in result.raw

        # Screenshot should be taken
        mock_page.screenshot.assert_called_once()

        # Publish button should NOT have been clicked -- the _click_first_match
        # for publish buttons happens after the dry_run check, so query_selector
        # calls should be limited to title/body insertion only.
        # We verify by checking wait_for_url was never called (only in real publish)
        mock_page.wait_for_url.assert_not_called()

    @patch("publishers.medium.sync_playwright")
    def test_publish_tags_capped_at_five(self, mock_sync_pw, tmp_path):
        session_path = _make_session_file(tmp_path)
        mock_pw, mock_pw_inst, mock_browser, mock_ctx, mock_page = _build_mock_chain()
        mock_sync_pw.return_value = mock_pw

        def set_published_url(*args, **kwargs):
            mock_page.url = "https://medium.com/@unice/test-abc"

        mock_page.wait_for_url.side_effect = set_published_url

        # Track typed tags
        typed_tags = []

        def track_type(text, **kwargs):
            typed_tags.append(text)

        mock_page.keyboard.type = MagicMock(side_effect=track_type)

        # query_selector returns tag_input for tag selectors
        original_qs = mock_page.query_selector.return_value
        mock_page.query_selector.return_value = original_qs

        art = _article(tags=["a", "b", "c", "d", "e", "f", "g"])
        pub = MediumPublisher(session_path=session_path)
        pub.publish(art, images=[])

        # The title + 5 tags should have been typed (not 7)
        # Title is typed once, then 5 tags
        tag_type_calls = [c for c in typed_tags if c in ["a", "b", "c", "d", "e", "f", "g"]]
        assert len(tag_type_calls) <= 5
        assert "f" not in tag_type_calls
        assert "g" not in tag_type_calls


class TestHtmlConversion:
    @patch("publishers.medium.sync_playwright")
    def test_publish_html_conversion(self, mock_sync_pw, tmp_path):
        session_path = _make_session_file(tmp_path)
        mock_pw, mock_pw_inst, mock_browser, mock_ctx, mock_page = _build_mock_chain()
        mock_sync_pw.return_value = mock_pw

        pub = MediumPublisher(session_path=session_path, dry_run=True)
        art = _article(body_markdown="**bold text** and `code`")
        pub.publish(art, images=[])

        # Verify page.evaluate was called with HTML (not raw markdown)
        evaluate_calls = mock_page.evaluate.call_args_list
        assert len(evaluate_calls) > 0
        # The HTML argument should contain <strong> from markdown conversion
        html_arg = evaluate_calls[0][0][1]  # second positional arg
        assert "<strong>" in html_arg or "<b>" in html_arg
        assert "<code>" in html_arg
