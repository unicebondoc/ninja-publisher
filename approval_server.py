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

from base import BasePublisher
from services import telegram_notify
from services.notion_client import NotionClient
from services.slack_handler import (
    ACTION_APPROVE,
    ACTION_EDIT,
    ACTION_HOOK_PREFIX,
    ACTION_REJECT,
    InteractionEvent,
    SlackHandler,
)

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
    expected = (
        "v0="
        + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    )
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
) -> Flask:
    app = Flask(__name__)
    app.config["SIGNING_SECRET"] = signing_secret or os.environ.get("SLACK_SIGNING_SECRET", "")
    app.config["SLACK"] = slack
    app.config["NOTION"] = notion
    app.config["NOTION_URL_TEMPLATE"] = notion_url_template
    app.config["MEDIUM_PUBLISHER"] = medium_publisher

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
        slack.post_to_response_url(
            response_url, "Publish failed: publisher not configured"
        )
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
            telegram.notify(
                f"Published <b>{article.title}</b>: {result.url}"
            )
        except Exception:  # noqa: BLE001 — fire-and-forget
            log.warning("telegram notify failed (non-fatal) for %s", page_id)

    except Exception as exc:  # noqa: BLE001 — catch-all for error path
        safe = sanitize_error(exc)
        try:
            notion.log_error(page_id, "medium", safe)
        except Exception:  # noqa: BLE001
            log.exception("failed to update Notion error state for %s", page_id)
        try:
            slack.post_to_response_url(
                response_url, f"Publish failed: {safe}"
            )
        except Exception:  # noqa: BLE001
            log.exception("failed to post error to Slack for %s", page_id)
        try:
            telegram.notify(
                f"Publish FAILED for {article_title}: {safe}", urgent=True
            )
        except Exception:  # noqa: BLE001 — fire-and-forget
            log.warning("telegram urgent notify failed for %s", page_id)


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


def _build_default_app() -> Flask:
    load_dotenv(override=False)
    from publishers.medium import MediumPublisher

    slack_handler: SlackHandler | None = None
    notion_client: NotionClient | None = None
    medium_pub: BasePublisher | None = None
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
    return create_app(
        slack=slack_handler,
        notion=notion_client,
        medium_publisher=medium_pub,
    )


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    app = _build_default_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
