import pytest

from base import Article
from services.slack_handler import (
    ACTION_APPROVE,
    ACTION_EDIT,
    ACTION_HOOK_PREFIX,
    ACTION_REJECT,
    InteractionEvent,
    SlackHandler,
    SlackHandlerError,
)


class FakeSlack:
    def __init__(self, post_resp=None):
        self.posted = []
        self.updated = []
        self.post_resp = post_resp if post_resp is not None else {"ok": True, "ts": "1700000000.000100"}

    def chat_postMessage(self, **kwargs):
        self.posted.append(kwargs)
        return self.post_resp

    def chat_update(self, **kwargs):
        self.updated.append(kwargs)
        return {"ok": True}


@pytest.fixture
def fake_slack():
    return FakeSlack()


@pytest.fixture
def handler(fake_slack):
    return SlackHandler(token="xoxb-t", channel_id="C0ASL49F2P8", client=fake_slack)


def _article_with_hooks(**overrides) -> Article:
    base = dict(
        title="AI Butler on Hetzner",
        slug="ai-butler",
        body_markdown="# body",
        notion_page_id="page_abc",
        hero_image_url="https://cdn.example/h.jpg",
        word_count=1800,
        reading_time_minutes=8,
        publish_to=["medium", "linkedin"],
        hook_options=[f"Hook {i}" for i in range(1, 11)],
    )
    base.update(overrides)
    return Article(**base)


def test_requires_token(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C0ASL49F2P8")
    with pytest.raises(SlackHandlerError, match="SLACK_BOT_TOKEN"):
        SlackHandler(client=FakeSlack())


def test_requires_channel(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "t")
    monkeypatch.delenv("SLACK_CHANNEL_ID", raising=False)
    with pytest.raises(SlackHandlerError, match="SLACK_CHANNEL_ID"):
        SlackHandler(client=FakeSlack())


def test_send_approval_card_requires_page_id(handler):
    a = _article_with_hooks(notion_page_id=None)
    with pytest.raises(SlackHandlerError, match="notion_page_id"):
        handler.send_approval_card(a)


def test_send_approval_card_returns_ts(handler, fake_slack):
    ts = handler.send_approval_card(_article_with_hooks())
    assert ts == "1700000000.000100"
    call = fake_slack.posted[0]
    assert call["channel"] == "C0ASL49F2P8"
    assert "AI Butler on Hetzner" in call["text"]


def test_send_approval_card_blocks_structure(handler, fake_slack):
    handler.send_approval_card(_article_with_hooks())
    blocks = fake_slack.posted[0]["blocks"]
    types = [b["type"] for b in blocks]
    assert types[0] == "image"
    assert blocks[0]["image_url"] == "https://cdn.example/h.jpg"
    assert "header" in types
    assert "context" in types
    assert types.count("actions") == 3  # 2 hook rows + decision row
    assert any(b.get("block_id") == "hooks_row_1" for b in blocks)
    assert any(b.get("block_id") == "hooks_row_2" for b in blocks)
    assert any(b.get("block_id") == "decision_row" for b in blocks)


def test_send_approval_card_meta_context(handler, fake_slack):
    handler.send_approval_card(_article_with_hooks())
    blocks = fake_slack.posted[0]["blocks"]
    context = next(b for b in blocks if b["type"] == "context")
    text = context["elements"][0]["text"]
    assert "1800 words" in text
    assert "8 min read" in text
    assert "medium, linkedin" in text


def test_send_approval_card_10_hook_buttons(handler, fake_slack):
    handler.send_approval_card(_article_with_hooks())
    blocks = fake_slack.posted[0]["blocks"]
    hook_rows = [b for b in blocks if b.get("block_id", "").startswith("hooks_row_")]
    all_btns = [btn for row in hook_rows for btn in row["elements"]]
    assert len(all_btns) == 10
    assert [b["action_id"] for b in all_btns] == [f"{ACTION_HOOK_PREFIX}{i}" for i in range(1, 11)]
    assert all(b["value"] == "page_abc" for b in all_btns)


def test_send_approval_card_decision_buttons(handler, fake_slack):
    handler.send_approval_card(_article_with_hooks())
    blocks = fake_slack.posted[0]["blocks"]
    decision = next(b for b in blocks if b.get("block_id") == "decision_row")
    ids = [e["action_id"] for e in decision["elements"]]
    assert ids == [ACTION_APPROVE, ACTION_EDIT, ACTION_REJECT]
    reject_btn = decision["elements"][2]
    assert reject_btn.get("confirm")  # destructive = confirm dialog


def test_send_approval_card_no_hooks_skips_hook_section(handler, fake_slack):
    handler.send_approval_card(_article_with_hooks(hook_options=[]))
    blocks = fake_slack.posted[0]["blocks"]
    assert not any(b.get("block_id", "").startswith("hooks_row_") for b in blocks)


def test_send_approval_card_raises_if_no_ts(fake_slack):
    fake_slack.post_resp = {"ok": True}
    h = SlackHandler(token="t", channel_id="C", client=fake_slack)
    with pytest.raises(SlackHandlerError, match="no ts"):
        h.send_approval_card(_article_with_hooks())


def test_update_card_status(handler, fake_slack):
    handler.update_card_status("1700000000.000100", "Publishing…")
    call = fake_slack.updated[0]
    assert call["channel"] == "C0ASL49F2P8"
    assert call["ts"] == "1700000000.000100"
    assert "Publishing" in call["text"]
    assert call["blocks"][0]["text"]["text"].startswith("*Status:*")


def _block_actions_payload(action_id: str, value: str = "page_abc") -> dict:
    return {
        "type": "block_actions",
        "user": {"id": "U42"},
        "container": {"message_ts": "1700000000.000100"},
        "response_url": "https://hooks.slack.com/actions/xyz",
        "actions": [{"action_id": action_id, "value": value, "type": "button"}],
    }


def test_parse_interaction_approve(handler):
    evt = handler.parse_interaction(_block_actions_payload(ACTION_APPROVE))
    assert isinstance(evt, InteractionEvent)
    assert evt.action_id == ACTION_APPROVE
    assert evt.user_id == "U42"
    assert evt.notion_page_id == "page_abc"
    assert evt.message_ts == "1700000000.000100"
    assert evt.selected_hook is None


def test_parse_interaction_edit(handler):
    evt = handler.parse_interaction(_block_actions_payload(ACTION_EDIT))
    assert evt.action_id == ACTION_EDIT


def test_parse_interaction_reject(handler):
    evt = handler.parse_interaction(_block_actions_payload(ACTION_REJECT))
    assert evt.action_id == ACTION_REJECT


def test_parse_interaction_hook_selection(handler):
    evt = handler.parse_interaction(_block_actions_payload(f"{ACTION_HOOK_PREFIX}3"))
    assert evt.selected_hook == 3


def test_parse_interaction_rejects_out_of_range_hook(handler):
    with pytest.raises(SlackHandlerError, match="out of range"):
        handler.parse_interaction(_block_actions_payload(f"{ACTION_HOOK_PREFIX}99"))


def test_parse_interaction_rejects_unknown_action(handler):
    with pytest.raises(SlackHandlerError, match="unknown action_id"):
        handler.parse_interaction(_block_actions_payload("delete_everything"))


def test_parse_interaction_rejects_non_block_actions(handler):
    with pytest.raises(SlackHandlerError, match="unsupported"):
        handler.parse_interaction({"type": "view_submission"})


def test_parse_interaction_rejects_empty_actions(handler):
    with pytest.raises(SlackHandlerError, match="no actions"):
        handler.parse_interaction({"type": "block_actions", "actions": []})


# ---- post_to_response_url (T13-T14) ----


def test_post_to_response_url_happy_path(handler, mocker):
    """T13: posts to response_url with replace_original."""
    mock_post = mocker.patch("services.slack_handler.requests.post")
    handler.post_to_response_url(
        "https://hooks.slack.com/actions/T00/B00/xxx",
        "Published: https://medium.com/p/123",
    )
    mock_post.assert_called_once_with(
        "https://hooks.slack.com/actions/T00/B00/xxx",
        json={
            "text": "Published: https://medium.com/p/123",
            "replace_original": True,
        },
        timeout=10,
    )


def test_post_to_response_url_ssrf_rejection(handler, mocker):
    """T14: rejects non-Slack URLs (SSRF guard)."""
    mock_post = mocker.patch("services.slack_handler.requests.post")
    with pytest.raises(ValueError, match="https://hooks.slack.com/"):
        handler.post_to_response_url("https://evil.com/hook", "pwned")
    mock_post.assert_not_called()
