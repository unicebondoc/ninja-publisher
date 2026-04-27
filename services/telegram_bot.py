"""Telegram approval bot — inline keyboard cards + long-polling for callbacks.

Uses the Telegram Bot API directly via requests. No framework dependency.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid as _uuid
from collections.abc import Callable
from typing import Any

import requests

TELEGRAM_API = "https://api.telegram.org"
NOTION_PAGE_URL_TEMPLATE = "https://www.notion.so/{page_id_nodashes}"

log = logging.getLogger("telegram_bot")


class TelegramBotError(RuntimeError):
    pass


class TelegramBot:
    """Sends approval cards and polls for callback query responses."""

    def __init__(
        self,
        *,
        bot_token: str | None = None,
        chat_id: str | None = None,
        session: requests.Session | None = None,
        timeout: int = 10,
    ):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        if not self.bot_token:
            raise TelegramBotError("TELEGRAM_BOT_TOKEN is not set")
        if not self.chat_id:
            raise TelegramBotError("TELEGRAM_CHAT_ID is not set")
        self._session = session or requests.Session()
        self._timeout = timeout
        self._base_url = f"{TELEGRAM_API}/bot{self.bot_token}"
        self._polling_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ---- public API ----

    def send_approval_card(self, article: Any, notion_url: str | None = None) -> dict:
        """Send a Telegram message with article preview + Approve/Reject buttons.

        Returns the Telegram API response body (the 'result' dict containing
        message_id, chat, etc).
        """
        page_id = article.notion_page_id
        if not page_id:
            raise TelegramBotError("article.notion_page_id is required")

        if notion_url is None:
            notion_url = NOTION_PAGE_URL_TEMPLATE.format(page_id_nodashes=page_id.replace("-", ""))

        text = self._build_card_text(article, notion_url)
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "\u2705 Approve",
                        "callback_data": f"approve:{page_id}",
                    },
                    {
                        "text": "\u274c Reject",
                        "callback_data": f"reject:{page_id}",
                    },
                ]
            ]
        }

        return self._send_message(text, reply_markup=keyboard)

    def send_message(self, chat_id: str | int, text: str) -> dict:
        """Send a plain text message to a chat. Returns the Telegram API response body."""
        resp = self._session.post(
            f"{self._base_url}/sendMessage",
            json={
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=self._timeout,
        )
        return self._handle_response(resp, "sendMessage")

    def edit_message(self, message_id: int, text: str) -> dict:
        """Edit an existing message (remove keyboard, update text)."""
        resp = self._session.post(
            f"{self._base_url}/editMessageText",
            json={
                "chat_id": self.chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=self._timeout,
        )
        return self._handle_response(resp, "editMessageText")

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> dict:
        """Acknowledge a callback query (required by Telegram API)."""
        resp = self._session.post(
            f"{self._base_url}/answerCallbackQuery",
            json={
                "callback_query_id": callback_query_id,
                "text": text,
            },
            timeout=self._timeout,
        )
        return self._handle_response(resp, "answerCallbackQuery")

    def start_polling(
        self,
        on_approve: Callable[[str, int, str], None],
        on_reject: Callable[[str, int, str], None],
        poll_timeout: int = 30,
        on_topic: Callable[[str, str, int], None] | None = None,
    ) -> None:
        """Start long-polling for callback queries in a daemon thread.

        Callbacks receive (page_id, message_id, callback_query_id).
        on_topic receives (chat_id, topic_text, message_id).
        """
        if self._polling_thread is not None and self._polling_thread.is_alive():
            log.warning("polling thread already running")
            return

        self._stop_event.clear()
        self._polling_thread = threading.Thread(
            target=self._poll_loop,
            args=(on_approve, on_reject, poll_timeout, on_topic),
            daemon=True,
            name="telegram-approval-poll",
        )
        self._polling_thread.start()
        log.info("Telegram approval polling started")

    def stop_polling(self) -> None:
        """Signal the polling thread to stop."""
        self._stop_event.set()
        if self._polling_thread is not None:
            self._polling_thread.join(timeout=5)
            self._polling_thread = None

    # ---- internal ----

    def _build_card_text(self, article: Any, notion_url: str) -> str:
        lines = [f"<b>{_escape_html(article.title)}</b>"]

        if article.subtitle:
            lines.append(f"<i>{_escape_html(article.subtitle)}</i>")

        meta_parts: list[str] = []
        if article.tags:
            meta_parts.append(" ".join(f"#{t}" for t in article.tags[:5]))
        if article.word_count is not None:
            meta_parts.append(f"{article.word_count} words")
        if article.reading_time_minutes is not None:
            meta_parts.append(f"{article.reading_time_minutes} min read")
        if meta_parts:
            lines.append(" | ".join(meta_parts))

        if article.publish_to:
            lines.append(f"Publish to: {', '.join(article.publish_to)}")

        lines.append(f'\n<a href="{notion_url}">Open in Notion</a>')

        return "\n".join(lines)

    def _send_message(self, text: str, reply_markup: dict | None = None) -> dict:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        resp = self._session.post(
            f"{self._base_url}/sendMessage",
            json=payload,
            timeout=self._timeout,
        )
        return self._handle_response(resp, "sendMessage")

    def _handle_response(self, resp: requests.Response, method: str) -> dict:
        if resp.status_code != 200:
            raise TelegramBotError(
                f"Telegram {method} failed: HTTP {resp.status_code} - {resp.text[:200]}"
            )
        body = resp.json()
        if not body.get("ok"):
            raise TelegramBotError(f"Telegram {method} returned ok=false: {body}")
        return body.get("result", {})

    def _poll_loop(
        self,
        on_approve: Callable[[str, int, str], None],
        on_reject: Callable[[str, int, str], None],
        poll_timeout: int,
        on_topic: Callable[[str, str, int], None] | None = None,
    ) -> None:
        allowed = '["callback_query","message"]' if on_topic else '["callback_query"]'
        offset = 0
        while not self._stop_event.is_set():
            try:
                resp = self._session.get(
                    f"{self._base_url}/getUpdates",
                    params={
                        "offset": offset,
                        "timeout": poll_timeout,
                        "allowed_updates": allowed,
                    },
                    timeout=poll_timeout + 10,
                )
                if resp.status_code != 200:
                    log.warning("getUpdates returned HTTP %s", resp.status_code)
                    time.sleep(2)
                    continue

                body = resp.json()
                if not body.get("ok"):
                    log.warning("getUpdates returned ok=false: %s", body)
                    time.sleep(2)
                    continue

                for update in body.get("result", []):
                    offset = update["update_id"] + 1
                    cb = update.get("callback_query")
                    if cb:
                        self._handle_callback(cb, on_approve, on_reject)
                        continue
                    msg = update.get("message")
                    if msg and on_topic:
                        self._handle_message(msg, on_topic)

            except requests.RequestException as exc:
                log.warning("polling error: %s", exc)
                if not self._stop_event.is_set():
                    time.sleep(5)
            except Exception:
                log.exception("unexpected error in poll loop")
                if not self._stop_event.is_set():
                    time.sleep(5)

    def _handle_message(
        self,
        msg: dict,
        on_topic: Callable[[str, str, int], None],
    ) -> None:
        """Handle an incoming text message.

        Drafting is gated behind ``/draft <topic>`` so casual chat doesn't
        accidentally kick off an article. ``/start`` and ``/help`` show usage.
        Anything else is ignored silently — leaves room for other bots
        (e.g. Butler) sharing the same chat.
        """
        text = (msg.get("text") or "").strip()
        if not text:
            return
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        message_id = msg.get("message_id", 0)
        # Only handle messages from the configured chat
        if chat_id != str(self.chat_id):
            log.debug("ignoring message from chat %s (expected %s)", chat_id, self.chat_id)
            return

        # Strip optional bot mention suffix: "/draft@MyBot foo" -> "/draft foo"
        first_token, _, rest = text.partition(" ")
        cmd = first_token.split("@", 1)[0].lower()

        if cmd == "/draft":
            topic = rest.strip()
            if not topic:
                self.send_message(chat_id, "Usage: <code>/draft &lt;topic&gt;</code>")
                return
            log.info("draft command from chat %s: %s", chat_id, topic[:80])
            on_topic(chat_id, topic, message_id)
            return

        if cmd in ("/start", "/help"):
            self.send_message(
                chat_id,
                "Ninja Publisher bot.\n\n"
                "<b>/draft &lt;topic&gt;</b> — draft an article and send for approval.\n"
                "Approve/Reject buttons appear on the draft card.",
            )
            return

        # Non-command messages: ignore silently.
        log.debug("ignoring non-command message from chat %s: %s", chat_id, text[:80])

    def _handle_callback(
        self,
        cb: dict,
        on_approve: Callable[[str, int, str], None],
        on_reject: Callable[[str, int, str], None],
    ) -> None:
        data = cb.get("data", "")
        callback_query_id = cb.get("id", "")
        message = cb.get("message", {})
        message_id = message.get("message_id", 0)

        if ":" not in data:
            log.warning("ignoring callback with unexpected data: %r", data)
            return

        action, page_id = data.split(":", 1)

        try:
            _uuid.UUID(page_id)
        except ValueError:
            log.warning("Invalid page_id in callback_data: %s", page_id[:50])
            return

        if action == "approve":
            log.info("approval callback for page %s", page_id)
            on_approve(page_id, message_id, callback_query_id)
        elif action == "reject":
            log.info("rejection callback for page %s", page_id)
            on_reject(page_id, message_id, callback_query_id)
        else:
            log.warning("unknown callback action: %r", action)


def _escape_html(text: str) -> str:
    """Escape HTML special chars for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
