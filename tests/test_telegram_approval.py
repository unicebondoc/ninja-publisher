"""Integration tests for the Telegram approval flow wired into approval_server."""

from unittest.mock import MagicMock

import pytest

from approval_server import (
    create_app,
    execute_telegram_publish,
    handle_telegram_reject,
)
from base import Article, PublishResult
from services.notion_client import NotionClient
from services.telegram_bot import TelegramBot


def _article(**overrides) -> Article:
    defaults = {
        "title": "Test Article",
        "slug": "test-article",
        "body_markdown": "Hello world.",
        "tags": ["python"],
        "notion_page_id": "page-abc-123",
        "publish_to": ["medium"],
    }
    defaults.update(overrides)
    return Article(**defaults)


@pytest.fixture
def notion():
    mock = MagicMock(spec=NotionClient)
    mock.PROPS = {"selected_hook": "Selected Hook"}
    return mock


@pytest.fixture
def tg_bot():
    mock = MagicMock(spec=TelegramBot)
    return mock


@pytest.fixture
def publisher():
    mock = MagicMock()
    mock.publish.return_value = PublishResult(
        platform="medium",
        url="https://medium.com/@test/article-123",
    )
    return mock


# ---- /trigger/check-ready ----


def test_check_ready_no_notion():
    app = create_app(signing_secret="s", telegram_bot=MagicMock(spec=TelegramBot))
    client = app.test_client()
    r = client.post("/trigger/check-ready")
    assert r.status_code == 500
    assert "notion" in r.get_json()["error"]


def test_check_ready_no_telegram_bot(notion):
    app = create_app(signing_secret="s", notion=notion)
    client = app.test_client()
    r = client.post("/trigger/check-ready")
    assert r.status_code == 500
    assert "telegram" in r.get_json()["error"]


def test_check_ready_sends_cards(notion, tg_bot):
    articles = [_article(notion_page_id="p1"), _article(notion_page_id="p2")]
    notion.query_rows_by_status.return_value = articles

    app = create_app(signing_secret="s", notion=notion, telegram_bot=tg_bot)
    client = app.test_client()
    r = client.post("/trigger/check-ready")

    assert r.status_code == 200
    body = r.get_json()
    assert body["sent"] == 2
    assert set(body["page_ids"]) == {"p1", "p2"}

    assert tg_bot.send_approval_card.call_count == 2
    # Notion status updated for each
    calls = notion.update_status.call_args_list
    assert any(c.args == ("p1", "Pending Approval") for c in calls)
    assert any(c.args == ("p2", "Pending Approval") for c in calls)


def test_check_ready_skips_no_page_id(notion, tg_bot):
    articles = [_article(notion_page_id=None), _article(notion_page_id="p1")]
    notion.query_rows_by_status.return_value = articles

    app = create_app(signing_secret="s", notion=notion, telegram_bot=tg_bot)
    client = app.test_client()
    r = client.post("/trigger/check-ready")

    body = r.get_json()
    assert body["sent"] == 1
    assert body["page_ids"] == ["p1"]


def test_check_ready_empty(notion, tg_bot):
    notion.query_rows_by_status.return_value = []

    app = create_app(signing_secret="s", notion=notion, telegram_bot=tg_bot)
    client = app.test_client()
    r = client.post("/trigger/check-ready")

    assert r.status_code == 200
    assert r.get_json()["sent"] == 0


def test_check_ready_notion_query_fails(notion, tg_bot):
    notion.query_rows_by_status.side_effect = RuntimeError("Notion down")

    app = create_app(signing_secret="s", notion=notion, telegram_bot=tg_bot)
    client = app.test_client()
    r = client.post("/trigger/check-ready")

    assert r.status_code == 500
    assert "notion query failed" in r.get_json()["error"]


def test_check_ready_card_send_failure_continues(notion, tg_bot):
    """If one card fails to send, the rest still get sent."""
    articles = [_article(notion_page_id="p1"), _article(notion_page_id="p2")]
    notion.query_rows_by_status.return_value = articles
    tg_bot.send_approval_card.side_effect = [RuntimeError("fail"), MagicMock()]

    app = create_app(signing_secret="s", notion=notion, telegram_bot=tg_bot)
    client = app.test_client()
    r = client.post("/trigger/check-ready")

    body = r.get_json()
    assert body["sent"] == 1
    assert body["page_ids"] == ["p2"]


# ---- execute_telegram_publish ----


def test_telegram_publish_success(notion, tg_bot, publisher):
    notion.get_status.return_value = "Publishing"
    notion.get_article.return_value = _article()

    execute_telegram_publish(notion, publisher, tg_bot, "page-abc-123", 42, "cb-001")

    tg_bot.answer_callback_query.assert_called_once_with("cb-001", "Publishing...")
    notion.update_status.assert_any_call("page-abc-123", "Publishing")
    notion.update_status.assert_any_call("page-abc-123", "Published")
    notion.save_platform_url.assert_called_once_with(
        "page-abc-123", "medium", "https://medium.com/@test/article-123"
    )
    # Final edit shows the published URL
    edit_calls = tg_bot.edit_message.call_args_list
    assert any("Published:" in str(c) for c in edit_calls)


def test_telegram_publish_no_publisher(notion, tg_bot):
    execute_telegram_publish(notion, None, tg_bot, "p1", 42, "cb-001")

    tg_bot.edit_message.assert_called_once_with(42, "Publish failed: publisher not configured")


def test_telegram_publish_double_tap_guard(notion, tg_bot, publisher):
    notion.get_status.return_value = "Published"  # already done

    execute_telegram_publish(notion, publisher, tg_bot, "p1", 42, "cb-001")

    publisher.publish.assert_not_called()


def test_telegram_publish_failure_updates_notion(notion, tg_bot, publisher):
    notion.get_status.return_value = "Publishing"
    notion.get_article.return_value = _article()
    publisher.publish.side_effect = RuntimeError("Medium API down")

    execute_telegram_publish(notion, publisher, tg_bot, "p1", 42, "cb-001")

    notion.log_error.assert_called_once()
    edit_calls = tg_bot.edit_message.call_args_list
    assert any("Publish failed:" in str(c) for c in edit_calls)


# ---- handle_telegram_reject ----


def test_telegram_reject(notion, tg_bot):
    handle_telegram_reject(notion, tg_bot, "page-xyz", 55, "cb-002")

    tg_bot.answer_callback_query.assert_called_once_with("cb-002", "Rejected")
    notion.update_status.assert_called_once_with("page-xyz", "Rejected")
    tg_bot.edit_message.assert_called_once_with(55, "Rejected")


def test_telegram_reject_notion_failure(notion, tg_bot):
    """Rejection handles Notion errors gracefully."""
    notion.update_status.side_effect = RuntimeError("Notion down")
    # Should not raise
    handle_telegram_reject(notion, tg_bot, "page-xyz", 55, "cb-002")
    tg_bot.answer_callback_query.assert_called_once()
