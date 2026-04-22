"""Slack approval webhook — Flask app. Verifies Slack signatures and
dispatches InteractionEvents to Notion + SlackHandler."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from typing import Any

from flask import Flask, Response, jsonify, request

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
) -> Flask:
    app = Flask(__name__)
    app.config["SIGNING_SECRET"] = signing_secret or os.environ.get("SLACK_SIGNING_SECRET", "")
    app.config["SLACK"] = slack
    app.config["NOTION"] = notion
    app.config["NOTION_URL_TEMPLATE"] = notion_url_template

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
        # Slack expects 200 within 3s. We do our dispatch sync for now
        # (each op is one Notion + one Slack API call — well under 3s); if
        # this grows, move to a thread + response_url flow.
        return jsonify(response_body)

    return app


def dispatch_action(app: Flask, event: InteractionEvent) -> dict[str, Any]:
    notion: NotionClient | None = app.config["NOTION"]
    slack: SlackHandler | None = app.config["SLACK"]
    tmpl: str = app.config["NOTION_URL_TEMPLATE"]

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
            slack.update_card_status(event.message_ts, "Publishing…")
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
    slack_handler: SlackHandler | None = None
    notion_client: NotionClient | None = None
    try:
        slack_handler = SlackHandler()
    except Exception as e:  # noqa: BLE001 — server must still boot for /health
        log.warning("SlackHandler init deferred: %s", e)
    try:
        notion_client = NotionClient()
    except Exception as e:  # noqa: BLE001
        log.warning("NotionClient init deferred: %s", e)
    return create_app(slack=slack_handler, notion=notion_client)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    app = _build_default_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
