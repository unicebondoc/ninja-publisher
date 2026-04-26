"""Tests for end-to-end approval orchestration (execute_publish + dispatch wiring)."""

from unittest.mock import MagicMock

from approval_server import execute_publish, sanitize_error
from base import Article, PublishResult

# ---- sanitize_error (T9) ----


def test_sanitize_error_redacts_secrets_and_truncates():
    """T9: Bearer tokens, internal URLs redacted; output <= 200 chars."""
    raw = (
        "Bearer sk-live-abc123 failed at "
        "https://internal.corp/api/v1/thing "
        "with token=xoxb-secret-value and "
        "https://medium.com/p/ok should stay "
        + "x" * 500
    )
    result = sanitize_error(raw)
    assert "sk-live-abc123" not in result
    assert "Bearer" not in result
    assert "token=xoxb" not in result
    assert "internal.corp" not in result
    assert "[REDACTED]" in result
    assert "[URL_REDACTED]" in result
    # medium.com URLs should NOT be redacted
    assert "medium.com" in result
    assert len(result) <= 200


# ---- execute_publish helpers ----

RESPONSE_URL = "https://hooks.slack.com/actions/T00/B00/xxx"
PAGE_ID = "page_abc"
ARTICLE_TITLE = "Test Article"


def _make_article() -> Article:
    return Article(
        title=ARTICLE_TITLE,
        slug="test-article",
        body_markdown="# Hello",
        notion_page_id=PAGE_ID,
    )


def _make_mocks(
    *,
    status: str = "Publishing",
    publish_raises: Exception | None = None,
    telegram_raises: Exception | None = None,
    notion_get_raises: Exception | None = None,
):
    notion = MagicMock()
    notion.get_status.return_value = status
    if notion_get_raises:
        notion.get_article.side_effect = notion_get_raises
    else:
        notion.get_article.return_value = _make_article()

    slack = MagicMock()
    publisher = MagicMock()
    if publish_raises:
        publisher.publish.side_effect = publish_raises
    else:
        publisher.publish.return_value = PublishResult(
            platform="medium",
            url="https://medium.com/p/published-123",
        )

    telegram = MagicMock()
    if telegram_raises:
        telegram.notify.side_effect = telegram_raises

    return notion, slack, publisher, telegram


# ---- execute_publish tests (T2-T7) ----


def test_execute_publish_happy_path():
    """T2: full success path — publish, Notion update, Slack reply, Telegram."""
    notion, slack, publisher, telegram = _make_mocks()

    execute_publish(
        notion, slack, publisher, telegram, PAGE_ID, RESPONSE_URL, ARTICLE_TITLE
    )

    notion.get_status.assert_called_once_with(PAGE_ID)
    notion.get_article.assert_called_once_with(PAGE_ID)
    publisher.publish.assert_called_once()
    notion.save_platform_url.assert_called_once_with(
        PAGE_ID, "medium", "https://medium.com/p/published-123"
    )
    notion.update_status.assert_called_once_with(PAGE_ID, "Published")
    slack.post_to_response_url.assert_called_once()
    assert "Published:" in slack.post_to_response_url.call_args.args[1]
    telegram.notify.assert_called_once()


def test_execute_publish_medium_failure():
    """T3: publisher raises — error path fires."""
    notion, slack, publisher, telegram = _make_mocks(
        publish_raises=RuntimeError("Medium 429 rate limited")
    )

    execute_publish(
        notion, slack, publisher, telegram, PAGE_ID, RESPONSE_URL, ARTICLE_TITLE
    )

    notion.log_error.assert_called_once()
    error_msg = notion.log_error.call_args.args[2]
    assert "rate limited" in error_msg.lower() or "429" in error_msg
    notion.update_status.assert_called_once_with(PAGE_ID, "Errored")
    slack.post_to_response_url.assert_called_once()
    assert "Publish failed:" in slack.post_to_response_url.call_args.args[1]
    telegram.notify.assert_called_once()
    assert telegram.notify.call_args.kwargs.get("urgent") is True


def test_execute_publish_telegram_failure_isolated():
    """T4: Telegram raises but publish still succeeds."""
    notion, slack, publisher, telegram = _make_mocks(
        telegram_raises=RuntimeError("Telegram down")
    )

    execute_publish(
        notion, slack, publisher, telegram, PAGE_ID, RESPONSE_URL, ARTICLE_TITLE
    )

    # Publish succeeded despite Telegram failure
    notion.save_platform_url.assert_called_once()
    notion.update_status.assert_called_once_with(PAGE_ID, "Published")
    slack.post_to_response_url.assert_called_once()
    assert "Published:" in slack.post_to_response_url.call_args.args[1]


def test_execute_publish_no_publisher():
    """T5: publisher is None — warning message to Slack."""
    notion, slack, _, telegram = _make_mocks()

    execute_publish(
        notion, slack, None, telegram, PAGE_ID, RESPONSE_URL, ARTICLE_TITLE
    )

    slack.post_to_response_url.assert_called_once_with(
        RESPONSE_URL, "Publish failed: publisher not configured"
    )
    notion.get_status.assert_not_called()
    notion.get_article.assert_not_called()


def test_execute_publish_notion_fetch_failure():
    """T6: Notion get_article raises — error path fires."""
    notion, slack, publisher, telegram = _make_mocks(
        notion_get_raises=RuntimeError("Notion API timeout")
    )

    execute_publish(
        notion, slack, publisher, telegram, PAGE_ID, RESPONSE_URL, ARTICLE_TITLE
    )

    publisher.publish.assert_not_called()
    notion.log_error.assert_called_once()
    notion.update_status.assert_called_once_with(PAGE_ID, "Errored")
    slack.post_to_response_url.assert_called_once()
    assert "Publish failed:" in slack.post_to_response_url.call_args.args[1]


def test_execute_publish_double_tap_guard():
    """T7: status is 'Published' (not 'Publishing') — no publish call."""
    notion, slack, publisher, telegram = _make_mocks(status="Published")

    execute_publish(
        notion, slack, publisher, telegram, PAGE_ID, RESPONSE_URL, ARTICLE_TITLE
    )

    notion.get_status.assert_called_once_with(PAGE_ID)
    notion.get_article.assert_not_called()
    publisher.publish.assert_not_called()
    slack.post_to_response_url.assert_not_called()
