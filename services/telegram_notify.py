"""Operational Telegram pings. Raw Bot API — no SDK dep needed."""

from __future__ import annotations

import os

import requests

TELEGRAM_API = "https://api.telegram.org"
URGENT_PREFIX = "⚠️ "


class TelegramError(RuntimeError):
    pass


def notify(
    message: str,
    urgent: bool = False,
    *,
    bot_token: str | None = None,
    chat_id: str | None = None,
    session: requests.Session | None = None,
    timeout: int = 10,
) -> None:
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
    if not chat:
        raise TelegramError("TELEGRAM_CHAT_ID is not set")

    text = f"{URGENT_PREFIX}{message}" if urgent else message
    http = session or requests
    r = http.post(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        json={
            "chat_id": chat,
            "text": text,
            "disable_notification": not urgent,
            "parse_mode": "HTML",
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        raise TelegramError(f"Telegram sendMessage failed: HTTP {r.status_code} — {r.text[:200]}")
    body = r.json()
    if not body.get("ok"):
        raise TelegramError(f"Telegram returned ok=false: {body}")
