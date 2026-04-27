"""Tests for services/telegram_bot.py — approval cards + callback polling."""

import json
from unittest.mock import MagicMock, patch

import pytest
import responses

from base import Article
from services.telegram_bot import (
    TELEGRAM_API,
    TelegramBot,
    TelegramBotError,
    _escape_html,
)

TOKEN = "000:test-token"
CHAT_ID = "7522106491"
BASE_URL = f"{TELEGRAM_API}/bot{TOKEN}"
SEND_URL = f"{BASE_URL}/sendMessage"
EDIT_URL = f"{BASE_URL}/editMessageText"
ANSWER_CB_URL = f"{BASE_URL}/answerCallbackQuery"
GET_UPDATES_URL = f"{BASE_URL}/getUpdates"


def _article(**overrides) -> Article:
    defaults = {
        "title": "Test Article",
        "slug": "test-article",
        "body_markdown": "Hello world.",
        "tags": ["python", "ai"],
        "subtitle": "A subtitle",
        "notion_page_id": "abc-def-123",
        "word_count": 500,
        "reading_time_minutes": 3,
        "publish_to": ["medium"],
    }
    defaults.update(overrides)
    return Article(**defaults)


def _bot(**kwargs) -> TelegramBot:
    defaults = {"bot_token": TOKEN, "chat_id": CHAT_ID}
    defaults.update(kwargs)
    return TelegramBot(**defaults)


# ---- init / env ----


def test_requires_bot_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    with pytest.raises(TelegramBotError, match="TELEGRAM_BOT_TOKEN"):
        TelegramBot()


def test_requires_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(TelegramBotError, match="TELEGRAM_CHAT_ID"):
        TelegramBot()


def test_uses_env_vars(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    bot = TelegramBot()
    assert bot.bot_token == TOKEN
    assert bot.chat_id == CHAT_ID


# ---- send_approval_card ----


@responses.activate
def test_send_approval_card_payload():
    responses.add(
        responses.POST,
        SEND_URL,
        json={"ok": True, "result": {"message_id": 42}},
        status=200,
    )
    bot = _bot()
    article = _article()
    result = bot.send_approval_card(article)

    assert result["message_id"] == 42
    assert len(responses.calls) == 1

    body = json.loads(responses.calls[0].request.body)
    assert body["chat_id"] == CHAT_ID
    assert body["parse_mode"] == "HTML"
    assert "Test Article" in body["text"]
    assert "#python" in body["text"]
    assert "500 words" in body["text"]
    assert "3 min read" in body["text"]
    assert "notion.so" in body["text"]

    keyboard = body["reply_markup"]["inline_keyboard"]
    assert len(keyboard) == 1
    assert len(keyboard[0]) == 2
    approve_btn, reject_btn = keyboard[0]
    assert approve_btn["callback_data"] == "approve:abc-def-123"
    assert reject_btn["callback_data"] == "reject:abc-def-123"
    assert "Approve" in approve_btn["text"]
    assert "Reject" in reject_btn["text"]


@responses.activate
def test_send_approval_card_custom_notion_url():
    responses.add(
        responses.POST,
        SEND_URL,
        json={"ok": True, "result": {"message_id": 10}},
        status=200,
    )
    bot = _bot()
    article = _article()
    bot.send_approval_card(article, notion_url="https://custom.notion.so/page")

    body = json.loads(responses.calls[0].request.body)
    assert "https://custom.notion.so/page" in body["text"]


@responses.activate
def test_send_approval_card_default_notion_url():
    responses.add(
        responses.POST,
        SEND_URL,
        json={"ok": True, "result": {"message_id": 10}},
        status=200,
    )
    bot = _bot()
    article = _article(notion_page_id="abc-def-123")
    bot.send_approval_card(article)

    body = json.loads(responses.calls[0].request.body)
    # Dashes stripped from page_id
    assert "notion.so/abcdef123" in body["text"]


def test_send_approval_card_requires_page_id():
    bot = _bot()
    article = _article(notion_page_id=None)
    with pytest.raises(TelegramBotError, match="notion_page_id"):
        bot.send_approval_card(article)


@responses.activate
def test_send_approval_card_minimal_article():
    """Article with no optional fields still produces a valid card."""
    responses.add(
        responses.POST,
        SEND_URL,
        json={"ok": True, "result": {"message_id": 1}},
        status=200,
    )
    bot = _bot()
    article = _article(
        subtitle=None,
        tags=[],
        word_count=None,
        reading_time_minutes=None,
        publish_to=[],
    )
    result = bot.send_approval_card(article)
    assert result["message_id"] == 1

    body = json.loads(responses.calls[0].request.body)
    assert "Test Article" in body["text"]


@responses.activate
def test_send_approval_card_http_error():
    responses.add(responses.POST, SEND_URL, status=500, body="Internal Server Error")
    bot = _bot()
    with pytest.raises(TelegramBotError, match="HTTP 500"):
        bot.send_approval_card(_article())


@responses.activate
def test_send_approval_card_ok_false():
    responses.add(
        responses.POST,
        SEND_URL,
        json={"ok": False, "description": "bad request"},
        status=200,
    )
    bot = _bot()
    with pytest.raises(TelegramBotError, match="ok=false"):
        bot.send_approval_card(_article())


# ---- edit_message ----


@responses.activate
def test_edit_message():
    responses.add(
        responses.POST,
        EDIT_URL,
        json={"ok": True, "result": {"message_id": 42}},
        status=200,
    )
    bot = _bot()
    result = bot.edit_message(42, "Updated text")

    body = json.loads(responses.calls[0].request.body)
    assert body["chat_id"] == CHAT_ID
    assert body["message_id"] == 42
    assert body["text"] == "Updated text"
    assert body["parse_mode"] == "HTML"
    assert result["message_id"] == 42


# ---- answer_callback_query ----


@responses.activate
def test_answer_callback_query():
    responses.add(
        responses.POST,
        ANSWER_CB_URL,
        json={"ok": True, "result": True},
        status=200,
    )
    bot = _bot()
    bot.answer_callback_query("cb123", "Done!")

    body = json.loads(responses.calls[0].request.body)
    assert body["callback_query_id"] == "cb123"
    assert body["text"] == "Done!"


# ---- callback parsing (_handle_callback) ----


def test_handle_callback_approve():
    bot = _bot()
    on_approve = MagicMock()
    on_reject = MagicMock()

    cb = {
        "id": "cb-001",
        "data": "approve:a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "message": {"message_id": 99},
    }
    bot._handle_callback(cb, on_approve, on_reject)

    on_approve.assert_called_once_with("a1b2c3d4-e5f6-7890-abcd-ef1234567890", 99, "cb-001")
    on_reject.assert_not_called()


def test_handle_callback_reject():
    bot = _bot()
    on_approve = MagicMock()
    on_reject = MagicMock()

    cb = {
        "id": "cb-002",
        "data": "reject:a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "message": {"message_id": 55},
    }
    bot._handle_callback(cb, on_approve, on_reject)

    on_reject.assert_called_once_with("a1b2c3d4-e5f6-7890-abcd-ef1234567890", 55, "cb-002")
    on_approve.assert_not_called()


def test_handle_callback_unknown_action():
    bot = _bot()
    on_approve = MagicMock()
    on_reject = MagicMock()

    cb = {
        "id": "cb-003",
        "data": "unknown:a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "message": {"message_id": 1},
    }
    bot._handle_callback(cb, on_approve, on_reject)

    on_approve.assert_not_called()
    on_reject.assert_not_called()


def test_handle_callback_invalid_uuid_rejected():
    """callback_data with non-UUID page_id is silently ignored."""
    bot = _bot()
    on_approve = MagicMock()
    on_reject = MagicMock()

    cb = {
        "id": "cb-005",
        "data": "approve:not-a-valid-uuid",
        "message": {"message_id": 1},
    }
    bot._handle_callback(cb, on_approve, on_reject)

    on_approve.assert_not_called()
    on_reject.assert_not_called()


def test_handle_callback_no_colon_ignored():
    bot = _bot()
    on_approve = MagicMock()
    on_reject = MagicMock()

    cb = {"id": "cb-004", "data": "garbage", "message": {"message_id": 1}}
    bot._handle_callback(cb, on_approve, on_reject)

    on_approve.assert_not_called()
    on_reject.assert_not_called()


# ---- polling ----


@responses.activate
def test_poll_loop_processes_callbacks():
    """Verify _poll_loop fetches updates and dispatches callbacks."""
    responses.add(
        responses.GET,
        GET_UPDATES_URL,
        json={
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "callback_query": {
                        "id": "cb-poll-1",
                        "data": "approve:a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "message": {"message_id": 77},
                    },
                },
                {
                    "update_id": 101,
                    "callback_query": {
                        "id": "cb-poll-2",
                        "data": "reject:b2c3d4e5-f6a7-8901-bcde-f12345678901",
                        "message": {"message_id": 78},
                    },
                },
            ],
        },
        status=200,
    )
    # Second call returns empty to let loop proceed, then we stop
    responses.add(
        responses.GET,
        GET_UPDATES_URL,
        json={"ok": True, "result": []},
        status=200,
    )

    bot = _bot()
    on_approve = MagicMock()
    on_reject = MagicMock()

    # Run one iteration then stop
    def _stop_after_calls(*args, **kwargs):
        if on_approve.call_count + on_reject.call_count >= 2:
            bot._stop_event.set()

    on_approve.side_effect = _stop_after_calls
    on_reject.side_effect = _stop_after_calls

    bot._poll_loop(on_approve, on_reject, poll_timeout=1)

    on_approve.assert_called_once_with("a1b2c3d4-e5f6-7890-abcd-ef1234567890", 77, "cb-poll-1")
    on_reject.assert_called_once_with("b2c3d4e5-f6a7-8901-bcde-f12345678901", 78, "cb-poll-2")


def test_start_polling_creates_daemon_thread():
    bot = _bot()
    on_approve = MagicMock()
    on_reject = MagicMock()

    with patch.object(bot, "_poll_loop"):
        bot.start_polling(on_approve, on_reject)
        assert bot._polling_thread is not None
        assert bot._polling_thread.daemon is True
        assert bot._polling_thread.name == "telegram-approval-poll"
        bot.stop_polling()


def test_stop_polling_sets_event():
    bot = _bot()
    bot._stop_event.clear()
    mock_thread = MagicMock()
    bot._polling_thread = mock_thread
    bot.stop_polling()
    assert bot._stop_event.is_set()
    mock_thread.join.assert_called_once()
    assert bot._polling_thread is None


# ---- escape_html ----


def test_escape_html():
    assert _escape_html("A & B <C> D") == "A &amp; B &lt;C&gt; D"
    assert _escape_html("no special") == "no special"


# ---- card text building ----


def test_card_text_includes_subtitle():
    bot = _bot()
    article = _article(subtitle="My Subtitle")
    text = bot._build_card_text(article, "https://notion.so/abc")
    assert "My Subtitle" in text


def test_card_text_escapes_html_in_title():
    bot = _bot()
    article = _article(title="A <b>Bold</b> & Title")
    text = bot._build_card_text(article, "https://notion.so/abc")
    assert "&lt;b&gt;Bold&lt;/b&gt;" in text
    assert "&amp;" in text
