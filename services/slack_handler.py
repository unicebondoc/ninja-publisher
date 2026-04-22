"""Slack approval UI — Block Kit card + interaction parser."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from slack_sdk import WebClient

from base import Article

ACTION_APPROVE = "approve_publish"
ACTION_EDIT = "edit_draft"
ACTION_REJECT = "reject_draft"
ACTION_HOOK_PREFIX = "hook_"
HOOK_ACTION_IDS = [f"{ACTION_HOOK_PREFIX}{i}" for i in range(1, 11)]
DECISION_ACTION_IDS = {ACTION_APPROVE, ACTION_EDIT, ACTION_REJECT}


class SlackHandlerError(RuntimeError):
    pass


@dataclass
class InteractionEvent:
    action_id: str
    user_id: str
    message_ts: str
    notion_page_id: str | None
    selected_hook: int | None = None
    response_url: str | None = None


class SlackHandler:
    def __init__(
        self,
        token: str | None = None,
        channel_id: str | None = None,
        *,
        client: Any = None,
    ):
        self.token = token or os.environ.get("SLACK_BOT_TOKEN")
        self.channel_id = channel_id or os.environ.get("SLACK_CHANNEL_ID")
        if not self.token:
            raise SlackHandlerError("SLACK_BOT_TOKEN is not set")
        if not self.channel_id:
            raise SlackHandlerError("SLACK_CHANNEL_ID is not set")
        self._client = client or WebClient(token=self.token)

    def send_approval_card(self, article: Article) -> str:
        if not article.notion_page_id:
            raise SlackHandlerError("article.notion_page_id required for approval card")
        blocks = self._build_blocks(article)
        resp = self._client.chat_postMessage(
            channel=self.channel_id,
            text=f"Approve publish: {article.title}",  # fallback for notifications
            blocks=blocks,
        )
        ts = _attr(resp, "ts")
        if not ts:
            raise SlackHandlerError(f"chat_postMessage returned no ts: {resp}")
        return ts

    def update_card_status(self, ts: str, new_status: str) -> None:
        self._client.chat_update(
            channel=self.channel_id,
            ts=ts,
            text=f"Status: {new_status}",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Status:* {new_status}"},
                }
            ],
        )

    def parse_interaction(self, payload: dict) -> InteractionEvent:
        if payload.get("type") != "block_actions":
            raise SlackHandlerError(f"unsupported interaction type: {payload.get('type')!r}")
        actions = payload.get("actions") or []
        if not actions:
            raise SlackHandlerError("no actions in interaction payload")
        action = actions[0]
        action_id = action.get("action_id") or ""
        value = action.get("value")

        selected_hook: int | None = None
        if action_id.startswith(ACTION_HOOK_PREFIX):
            try:
                selected_hook = int(action_id.removeprefix(ACTION_HOOK_PREFIX))
            except ValueError as e:
                raise SlackHandlerError(f"bad hook action_id: {action_id!r}") from e
            if not (1 <= selected_hook <= 10):
                raise SlackHandlerError(f"hook index out of range: {selected_hook}")
        elif action_id not in DECISION_ACTION_IDS:
            raise SlackHandlerError(f"unknown action_id: {action_id!r}")

        user_id = (payload.get("user") or {}).get("id", "")
        container = payload.get("container") or {}
        message_ts = container.get("message_ts") or (payload.get("message") or {}).get("ts") or ""
        response_url = payload.get("response_url")
        return InteractionEvent(
            action_id=action_id,
            user_id=user_id,
            message_ts=message_ts,
            notion_page_id=value,
            selected_hook=selected_hook,
            response_url=response_url,
        )

    # ---- blocks ----

    def _build_blocks(self, article: Article) -> list[dict]:
        page_id = article.notion_page_id
        blocks: list[dict] = []

        if article.hero_image_url:
            blocks.append({
                "type": "image",
                "image_url": article.hero_image_url,
                "alt_text": f"Hero for {article.title}",
            })

        meta_bits: list[str] = []
        if article.word_count is not None:
            meta_bits.append(f"{article.word_count} words")
        if article.reading_time_minutes is not None:
            meta_bits.append(f"{article.reading_time_minutes} min read")
        if article.publish_to:
            meta_bits.append("→ " + ", ".join(article.publish_to))
        meta = " · ".join(meta_bits) if meta_bits else "(no metadata)"

        blocks.append({
            "type": "header",
            "text": {"type": "plain_text", "text": article.title[:150]},
        })
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": meta}],
        })

        hooks = (article.hook_options or [])[:10]
        if hooks:
            hook_md = "\n".join(f"*{i+1}.* {h}" for i, h in enumerate(hooks))
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*LinkedIn hooks:*\n{hook_md}"},
            })
            # 2 rows of 5 numbered buttons
            row1, row2 = [], []
            for i in range(len(hooks)):
                btn = {
                    "type": "button",
                    "action_id": f"{ACTION_HOOK_PREFIX}{i+1}",
                    "text": {"type": "plain_text", "text": str(i + 1)},
                    "value": page_id,
                }
                (row1 if i < 5 else row2).append(btn)
            if row1:
                blocks.append({"type": "actions", "block_id": "hooks_row_1", "elements": row1})
            if row2:
                blocks.append({"type": "actions", "block_id": "hooks_row_2", "elements": row2})

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "actions",
            "block_id": "decision_row",
            "elements": [
                {
                    "type": "button",
                    "action_id": ACTION_APPROVE,
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve & Publish"},
                    "value": page_id,
                },
                {
                    "type": "button",
                    "action_id": ACTION_EDIT,
                    "text": {"type": "plain_text", "text": "Edit draft"},
                    "value": page_id,
                },
                {
                    "type": "button",
                    "action_id": ACTION_REJECT,
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "value": page_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Reject this draft?"},
                        "text": {"type": "mrkdwn", "text": "Status will be set to *Rejected* in Notion."},
                        "confirm": {"type": "plain_text", "text": "Reject"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
            ],
        })
        return blocks


def _attr(resp: Any, key: str) -> Any:
    """slack_sdk responses are dict-like; handle both raw dicts and SlackResponse."""
    if isinstance(resp, dict):
        return resp.get(key)
    try:
        return resp[key]
    except (KeyError, TypeError):
        return getattr(resp, key, None)
