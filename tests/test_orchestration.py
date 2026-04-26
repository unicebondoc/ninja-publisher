"""Tests for end-to-end approval orchestration (execute_publish + dispatch wiring)."""

import hashlib
import hmac
import json
import time
import urllib.parse
from unittest.mock import MagicMock, patch

from approval_server import create_app, execute_publish, sanitize_error
from base import Article, PublishResult
from services.slack_handler import ACTION_APPROVE, SlackHandler

SIGNING_SECRET = "test-signing-secret"


def _sign(body: bytes, ts: str | None = None) -> tuple[str, str]:
    ts = ts or str(int(time.time()))
    base = b"v0:" + ts.encode() + b":" + body
    sig = "v0=" + hmac.new(SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    return ts, sig


def _form_body(payload: dict) -> bytes:
    return urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()


def _payload(action_id: str, value: str = "page_abc") -> dict:
    return {
        "type": "block_actions",
        "user": {"id": "U42"},
        "container": {"message_ts": "1700000000.000100"},
        "response_url": "https://hooks.slack.com/actions/T00/B00/xxx",
        "actions": [{"action_id": action_id, "value": value, "type": "button"}],
    }


# ---- T1: dispatch_action spawns thread ----


def test_approve_spawns_daemon_thread_and_returns_immediately():
    """T1: POST approve returns 200 immediately, thread started with daemon=True."""
    slack = MagicMock(spec=SlackHandler)
    real_handler = SlackHandler(token="t", channel_id="C", client=MagicMock())
    slack.parse_interaction.side_effect = real_handler.parse_interaction

    notion = MagicMock()

    app = create_app(
        signing_secret=SIGNING_SECRET,
        slack=slack,
        notion=notion,
    )
    client = app.test_client()
    body = _form_body(_payload(ACTION_APPROVE))
    ts, sig = _sign(body)

    with patch("approval_server.threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        r = client.post(
            "/slack/interact",
            data=body,
            headers={
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    assert r.status_code == 200
    assert r.get_json()["text"] == "publishing queued"

    # Thread was created with daemon=True and started
    mock_thread_cls.assert_called_once()
    call_kwargs = mock_thread_cls.call_args
    assert call_kwargs.kwargs["daemon"] is True
    assert call_kwargs.kwargs["target"] is execute_publish
    mock_thread.start.assert_called_once()

    # Sync steps still happened
    notion.update_status.assert_called_once_with("page_abc", "Publishing")
    slack.update_card_status.assert_called_once()


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
    # log_error already sets status to "Errored" internally — no separate call
    notion.update_status.assert_not_called()
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
    # log_error already sets status to "Errored" internally — no separate call
    notion.update_status.assert_not_called()
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
