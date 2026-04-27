"""Slack approval webhook — Flask app. Verifies Slack signatures and
dispatches InteractionEvents to Notion + SlackHandler."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from typing import Any

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request

from base import Article, BasePublisher
from services import telegram_notify
from services.article_drafter import ArticleDrafter
from services.image_gen import generate_image
from services.notion_client import NotionClient
from services.slack_handler import (
    ACTION_APPROVE,
    ACTION_EDIT,
    ACTION_HOOK_PREFIX,
    ACTION_REJECT,
    InteractionEvent,
    SlackHandler,
)
from services.telegram_bot import TelegramBot, TelegramBotError

VERSION = "0.2.0"
SLACK_REPLAY_WINDOW_SECONDS = 60 * 5
NOTION_PAGE_URL_TEMPLATE = "https://www.notion.so/{page_id_nodashes}"

log = logging.getLogger("approval_server")

_RE_BEARER_TOKEN = re.compile(r"(Bearer\s+\S+|token=\S+)")
_RE_INTERNAL_URL = re.compile(
    r"https?://(?!medium\.com[/\s]|medium\.com$|hooks\.slack\.com[/\s]|hooks\.slack\.com$)\S+"
)
_SANITIZE_MAX_LEN = 200


def sanitize_error(error: Exception | str) -> str:
    """Redact secrets and internal URLs from error messages for Slack output."""
    text = str(error)
    text = _RE_BEARER_TOKEN.sub("[REDACTED]", text)
    text = _RE_INTERNAL_URL.sub("[URL_REDACTED]", text)
    if len(text) > _SANITIZE_MAX_LEN:
        text = text[:_SANITIZE_MAX_LEN]
    return text


class SignatureError(ValueError):
    pass


def verify_slack_signature(
    signing_secret: str,
    timestamp: str | None,
    signature: str | None,
    raw_body: bytes,
    *,
    now: float | None = None,
) -> None:
    if not signing_secret:
        raise SignatureError("signing secret not configured")
    if not timestamp or not signature:
        raise SignatureError("missing X-Slack-Signature or X-Slack-Request-Timestamp")
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError) as e:
        raise SignatureError(f"bad timestamp: {timestamp!r}") from e
    current = now if now is not None else time.time()
    if abs(current - ts_int) > SLACK_REPLAY_WINDOW_SECONDS:
        raise SignatureError("timestamp outside replay window")
    basestring = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("signature mismatch")


def parse_slack_form(raw_body: bytes) -> dict:
    """Slack posts application/x-www-form-urlencoded with payload=<JSON>."""
    form = urllib.parse.parse_qs(raw_body.decode("utf-8"))
    payloads = form.get("payload") or []
    if not payloads:
        raise ValueError("no payload in Slack form body")
    return json.loads(payloads[0])


def create_app(
    *,
    signing_secret: str | None = None,
    slack: SlackHandler | None = None,
    notion: NotionClient | None = None,
    notion_url_template: str = NOTION_PAGE_URL_TEMPLATE,
    medium_publisher: BasePublisher | None = None,
    telegram_bot: TelegramBot | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["SIGNING_SECRET"] = signing_secret or os.environ.get("SLACK_SIGNING_SECRET", "")
    app.config["SLACK"] = slack
    app.config["NOTION"] = notion
    app.config["NOTION_URL_TEMPLATE"] = notion_url_template
    app.config["MEDIUM_PUBLISHER"] = medium_publisher
    app.config["TELEGRAM_BOT"] = telegram_bot

    @app.get("/health")
    def health() -> Response:
        return jsonify({"status": "ok", "version": VERSION})

    @app.post("/slack/interact")
    def interact() -> tuple[Response, int] | Response:
        raw_body = request.get_data(cache=False)
        try:
            verify_slack_signature(
                app.config["SIGNING_SECRET"],
                request.headers.get("X-Slack-Request-Timestamp"),
                request.headers.get("X-Slack-Signature"),
                raw_body,
            )
        except SignatureError as e:
            log.warning("slack signature rejected: %s", e)
            return jsonify({"error": "unauthorized", "reason": str(e)}), 401

        try:
            payload = parse_slack_form(raw_body)
        except (ValueError, json.JSONDecodeError) as e:
            log.warning("slack payload parse failed: %s", e)
            return jsonify({"error": "bad_payload", "reason": str(e)}), 400

        slack_handler: SlackHandler | None = app.config["SLACK"]
        if slack_handler is None:
            log.error("SlackHandler not configured")
            return jsonify({"error": "not_configured"}), 500
        try:
            event = slack_handler.parse_interaction(payload)
        except Exception as e:  # noqa: BLE001 — parse errors are user-caused
            log.warning("slack interaction parse failed: %s", e)
            return jsonify({"error": "bad_interaction", "reason": str(e)}), 400

        try:
            response_body = dispatch_action(app, event)
        except Exception:  # noqa: BLE001 — never 500 to Slack; log + ack
            log.exception("dispatch_action failed for action_id=%s", event.action_id)
            response_body = {"text": ":warning: action failed — check server logs"}
        # Slack expects 200 within 3s; heavy work (publish) runs in a
        # background thread that posts back via response_url.
        return jsonify(response_body)

    @app.post("/trigger/check-ready")
    def check_ready() -> tuple[Response, int] | Response:
        """Query Notion for Ready articles and send Telegram approval cards."""
        trigger_secret = os.environ.get("TRIGGER_SECRET", "")
        if trigger_secret:
            provided = request.headers.get("X-Trigger-Secret", "")
            if not hmac.compare_digest(trigger_secret, provided):
                return jsonify({"error": "unauthorized"}), 401

        notion_client: NotionClient | None = app.config["NOTION"]
        tg_bot: TelegramBot | None = app.config["TELEGRAM_BOT"]
        tmpl: str = app.config["NOTION_URL_TEMPLATE"]

        if notion_client is None:
            return jsonify({"error": "notion not configured"}), 500
        if tg_bot is None:
            return jsonify({"error": "telegram bot not configured"}), 500

        try:
            articles = notion_client.query_rows_by_status("Ready")
        except Exception as exc:  # noqa: BLE001
            log.exception("check-ready: failed to query Notion")
            return jsonify({"error": "notion query failed", "detail": sanitize_error(exc)}), 500

        sent = []
        for article in articles:
            if not article.notion_page_id:
                continue
            try:
                notion_url = tmpl.format(page_id_nodashes=article.notion_page_id.replace("-", ""))
                tg_bot.send_approval_card(article, notion_url=notion_url)
                notion_client.update_status(article.notion_page_id, "Pending Approval")
                sent.append(article.notion_page_id)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "check-ready: failed to send card for %s: %s",
                    article.notion_page_id,
                    exc,
                )

        return jsonify({"sent": len(sent), "page_ids": sent})

    return app


def execute_publish(
    notion: NotionClient,
    slack: SlackHandler,
    publisher: BasePublisher | None,
    telegram: Any,
    page_id: str,
    response_url: str,
    article_title: str,
) -> None:
    """Background thread target: fetch article, publish, update status."""
    if publisher is None:
        slack.post_to_response_url(response_url, "Publish failed: publisher not configured")
        return

    try:
        # Double-tap guard: abort if status moved away from Publishing
        status = notion.get_status(page_id)
        if status != "Publishing":
            log.info(
                "double-tap guard: page %s status is %r, skipping publish",
                page_id,
                status,
            )
            return

        article = notion.get_article(page_id)
        result = publisher.publish(article, images=[])

        # Success path
        notion.save_platform_url(page_id, "medium", result.url)
        notion.update_status(page_id, "Published")
        slack.post_to_response_url(response_url, f"Published: {result.url}")
        try:
            telegram.notify(f"Published <b>{article.title}</b>: {result.url}")
        except Exception:  # noqa: BLE001 — fire-and-forget
            log.warning("telegram notify failed (non-fatal) for %s", page_id)

    except Exception as exc:  # noqa: BLE001 — catch-all for error path
        safe = sanitize_error(exc)
        try:
            notion.log_error(page_id, "medium", safe)
        except Exception:  # noqa: BLE001
            log.exception("failed to update Notion error state for %s", page_id)
        try:
            slack.post_to_response_url(response_url, f"Publish failed: {safe}")
        except Exception:  # noqa: BLE001
            log.exception("failed to post error to Slack for %s", page_id)
        try:
            telegram.notify(f"Publish FAILED for {article_title}: {safe}", urgent=True)
        except Exception:  # noqa: BLE001 — fire-and-forget
            log.warning("telegram urgent notify failed for %s", page_id)


def execute_telegram_publish(
    notion: NotionClient,
    publisher: BasePublisher | None,
    tg_bot: TelegramBot,
    page_id: str,
    message_id: int,
    callback_query_id: str,
) -> None:
    """Background thread target: publish via Telegram approval callback."""
    try:
        tg_bot.answer_callback_query(callback_query_id, "Publishing...")
    except Exception:  # noqa: BLE001
        log.warning("failed to answer callback query for %s", page_id)

    if publisher is None:
        tg_bot.edit_message(message_id, "Publish failed: publisher not configured")
        return

    try:
        # Double-tap guard: status is set to "Publishing" in on_approve
        # before this thread starts. If it changed, another thread handled it.
        status = notion.get_status(page_id)
        if status != "Publishing":
            log.info(
                "double-tap guard: page %s status is %r, skipping publish",
                page_id,
                status,
            )
            return

        tg_bot.edit_message(message_id, "Publishing...")

        article = notion.get_article(page_id)
        result = publisher.publish(article, images=[])

        # Success
        notion.save_platform_url(page_id, "medium", result.url)
        notion.update_status(page_id, "Published")
        tg_bot.edit_message(message_id, f"Published: {result.url}")
        try:
            # telegram_notify used as module import (not parameter) because this
            # path has no Slack response_url — the ops channel notification is
            # fire-and-forget and does not need injection for testability.
            telegram_notify.notify(f"Published <b>{article.title}</b>: {result.url}")
        except Exception:  # noqa: BLE001
            log.warning("telegram notify failed (non-fatal) for %s", page_id)

    except Exception as exc:  # noqa: BLE001
        safe = sanitize_error(exc)
        try:
            notion.log_error(page_id, "medium", safe)
        except Exception:  # noqa: BLE001
            log.exception("failed to update Notion error state for %s", page_id)
        try:
            tg_bot.edit_message(message_id, f"Publish failed: {safe}")
        except Exception:  # noqa: BLE001
            log.exception("failed to edit Telegram message for %s", page_id)
        try:
            telegram_notify.notify(f"Publish FAILED for {page_id}: {safe}", urgent=True)
        except Exception:  # noqa: BLE001
            log.warning("telegram urgent notify failed for %s", page_id)


def handle_telegram_reject(
    notion: NotionClient,
    tg_bot: TelegramBot,
    page_id: str,
    message_id: int,
    callback_query_id: str,
) -> None:
    """Handle a Telegram rejection callback."""
    try:
        tg_bot.answer_callback_query(callback_query_id, "Rejected")
    except Exception:  # noqa: BLE001
        log.warning("failed to answer callback query for %s", page_id)

    try:
        notion.update_status(page_id, "Rejected")
        tg_bot.edit_message(message_id, "Rejected")
    except Exception as exc:  # noqa: BLE001
        log.exception("telegram reject failed for %s: %s", page_id, exc)


def dispatch_action(app: Flask, event: InteractionEvent) -> dict[str, Any]:
    notion: NotionClient | None = app.config["NOTION"]
    slack: SlackHandler | None = app.config["SLACK"]
    tmpl: str = app.config["NOTION_URL_TEMPLATE"]
    publisher: BasePublisher | None = app.config.get("MEDIUM_PUBLISHER")

    if not event.notion_page_id:
        return {"text": ":warning: no Notion page id attached to action"}

    if event.action_id.startswith(ACTION_HOOK_PREFIX):
        if notion is not None and event.selected_hook is not None:
            notion.save_selected_hook(event.notion_page_id, event.selected_hook)
        if slack is not None:
            slack.update_card_status(event.message_ts, f"Hook {event.selected_hook} selected")
        return {"text": f"hook {event.selected_hook} selected"}

    if event.action_id == ACTION_APPROVE:
        if notion is not None:
            notion.update_status(event.notion_page_id, "Publishing")
        if slack is not None:
            slack.update_card_status(event.message_ts, "Publishing\u2026")
        if notion is not None and slack is not None and event.response_url:
            threading.Thread(
                target=execute_publish,
                args=(
                    notion,
                    slack,
                    publisher,
                    telegram_notify,
                    event.notion_page_id,
                    event.response_url,
                    event.notion_page_id,  # article_title fallback
                ),
                daemon=True,
            ).start()
        return {"text": "publishing queued"}

    if event.action_id == ACTION_EDIT:
        url = tmpl.format(page_id_nodashes=event.notion_page_id.replace("-", ""))
        return {"text": f"edit draft: {url}"}

    if event.action_id == ACTION_REJECT:
        if notion is not None:
            notion.update_status(event.notion_page_id, "Rejected")
        if slack is not None:
            slack.update_card_status(event.message_ts, "Rejected")
        return {"text": "rejected"}

    return {"text": f":warning: unhandled action: {event.action_id}"}


def handle_draft_request(
    notion: NotionClient,
    tg_bot: TelegramBot,
    drafter: ArticleDrafter,
    chat_id: str,
    topic: str,
    message_id: int,
) -> None:
    """Handle a topic request: draft article, gen image, save to Notion, send approval card."""
    status_msg_id: int | None = None
    try:
        # 1. Acknowledge with a status message
        result = tg_bot.send_message(chat_id, f"Drafting article on: {topic[:100]}")
        status_msg_id = result.get("message_id")

        # 2. Draft via Claude CLI
        article = drafter.draft(topic)

        # 3. Update status
        if status_msg_id:
            tg_bot.edit_message(status_msg_id, "Generating hero image...")

        # 4. Generate hero image via MiniMax (optional — failure is non-fatal)
        try:
            image_bytes = generate_image(article.title)
            # For now we don't have image upload; hero_image_url stays None
            # Future: upload image_bytes to a CDN and set hero_image_url
            _ = image_bytes
        except Exception:  # noqa: BLE001
            log.info("hero image generation skipped (non-fatal) for %r", article.title)

        # 5. Save to Notion
        if status_msg_id:
            tg_bot.edit_message(status_msg_id, "Saving to Notion...")
        page_id = notion.save_draft(article)
        notion.update_status(page_id, "Ready")
        article = Article(**{**article.__dict__, "notion_page_id": page_id})

        # 6. Update status message
        if status_msg_id:
            tg_bot.edit_message(status_msg_id, "Draft ready! Sending for approval...")

        # 7. Send approval card
        notion_url = NOTION_PAGE_URL_TEMPLATE.format(page_id_nodashes=page_id.replace("-", ""))
        tg_bot.send_approval_card(article, notion_url=notion_url)

    except Exception as exc:  # noqa: BLE001
        error_msg = f"Draft failed: {sanitize_error(exc)}"
        log.exception("handle_draft_request failed for topic %r", topic[:80])
        try:
            tg_bot.send_message(chat_id, error_msg)
        except Exception:  # noqa: BLE001
            log.warning("failed to send error message to Telegram")


def _build_default_app() -> Flask:
    load_dotenv(override=False)
    from publishers.medium import MediumPublisher

    slack_handler: SlackHandler | None = None
    notion_client: NotionClient | None = None
    medium_pub: BasePublisher | None = None
    tg_bot: TelegramBot | None = None
    try:
        slack_handler = SlackHandler()
    except Exception as e:  # noqa: BLE001 — server must still boot for /health
        log.warning("SlackHandler init deferred: %s", e)
    try:
        notion_client = NotionClient()
    except Exception as e:  # noqa: BLE001
        log.warning("NotionClient init deferred: %s", e)
    try:
        medium_pub = MediumPublisher()
    except Exception as e:  # noqa: BLE001
        log.warning("MediumPublisher init deferred: %s", e)
    try:
        tg_bot = TelegramBot()
    except TelegramBotError as e:
        log.warning("TelegramBot init deferred: %s", e)

    app = create_app(
        slack=slack_handler,
        notion=notion_client,
        medium_publisher=medium_pub,
        telegram_bot=tg_bot,
    )

    # Start Telegram approval polling if both bot and notion are available
    if tg_bot is not None and notion_client is not None:
        _start_telegram_polling(app, tg_bot, notion_client, medium_pub)

    return app


def _start_telegram_polling(
    app: Flask,
    tg_bot: TelegramBot,
    notion: NotionClient,
    publisher: BasePublisher | None,
) -> None:
    """Wire Telegram callback handlers and start long-polling."""

    def on_approve(page_id: str, message_id: int, callback_query_id: str) -> None:
        notion.update_status(page_id, "Publishing")
        threading.Thread(
            target=execute_telegram_publish,
            args=(notion, publisher, tg_bot, page_id, message_id, callback_query_id),
            daemon=True,
        ).start()

    def on_reject(page_id: str, message_id: int, callback_query_id: str) -> None:
        threading.Thread(
            target=handle_telegram_reject,
            args=(notion, tg_bot, page_id, message_id, callback_query_id),
            daemon=True,
        ).start()

    drafter = ArticleDrafter()

    def on_topic(chat_id: str, topic_text: str, message_id: int) -> None:
        threading.Thread(
            target=handle_draft_request,
            args=(notion, tg_bot, drafter, chat_id, topic_text, message_id),
            daemon=True,
        ).start()

    tg_bot.start_polling(on_approve, on_reject, on_topic=on_topic)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    app = _build_default_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
