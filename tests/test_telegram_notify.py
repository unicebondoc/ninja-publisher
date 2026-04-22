import json

import pytest
import responses

from services.telegram_notify import TELEGRAM_API, URGENT_PREFIX, TelegramError, notify

SEND_URL = f"{TELEGRAM_API}/bot000:test-token/sendMessage"


def test_requires_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7522106491")
    with pytest.raises(TelegramError, match="TELEGRAM_BOT_TOKEN"):
        notify("hello")


def test_requires_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "000:test-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(TelegramError, match="TELEGRAM_CHAT_ID"):
        notify("hello")


@responses.activate
def test_normal_ping_is_silent():
    responses.add(responses.POST, SEND_URL, json={"ok": True, "result": {}}, status=200)
    notify("a quiet ping", bot_token="000:test-token", chat_id="c1")
    body = json.loads(responses.calls[0].request.body)
    assert body["chat_id"] == "c1"
    assert body["text"] == "a quiet ping"
    assert body["disable_notification"] is True
    assert body["parse_mode"] == "HTML"


@responses.activate
def test_urgent_ping_has_prefix_and_notifies():
    responses.add(responses.POST, SEND_URL, json={"ok": True, "result": {}}, status=200)
    notify("publisher crashed", urgent=True, bot_token="000:test-token", chat_id="c1")
    body = json.loads(responses.calls[0].request.body)
    assert body["text"] == f"{URGENT_PREFIX}publisher crashed"
    assert body["disable_notification"] is False


@responses.activate
def test_http_error_raises():
    responses.add(responses.POST, SEND_URL, status=429, body='{"ok":false,"description":"Too Many Requests"}')
    with pytest.raises(TelegramError, match="HTTP 429"):
        notify("x", bot_token="000:test-token", chat_id="c1")


@responses.activate
def test_ok_false_raises():
    responses.add(responses.POST, SEND_URL, status=200, json={"ok": False, "description": "chat not found"})
    with pytest.raises(TelegramError, match="ok=false"):
        notify("x", bot_token="000:test-token", chat_id="c1")


@responses.activate
def test_uses_env_vars_when_no_explicit_args(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "000:test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
    responses.add(responses.POST, SEND_URL, json={"ok": True, "result": {}}, status=200)
    notify("hi")
    body = json.loads(responses.calls[0].request.body)
    assert body["chat_id"] == "env-chat"
