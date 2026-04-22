import hashlib
import hmac
import json
import time
import urllib.parse
from unittest.mock import MagicMock

import pytest

from approval_server import (
    VERSION,
    SignatureError,
    create_app,
    parse_slack_form,
    verify_slack_signature,
)
from services.slack_handler import (
    ACTION_APPROVE,
    ACTION_EDIT,
    ACTION_HOOK_PREFIX,
    ACTION_REJECT,
    SlackHandler,
)

SIGNING_SECRET = "shh-it-is-a-secret"


def _sign(body: bytes, ts: str | None = None) -> tuple[str, str]:
    ts = ts or str(int(time.time()))
    base = b"v0:" + ts.encode() + b":" + body
    sig = "v0=" + hmac.new(SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    return ts, sig


def _form_body(payload: dict) -> bytes:
    return urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()


def _payload(action_id: str, value: str = "page_abc") -> dict:
    return {
        "type": "block_actions",
        "user": {"id": "U42"},
        "container": {"message_ts": "1700000000.000100"},
        "actions": [{"action_id": action_id, "value": value, "type": "button"}],
    }


# ---- verify_slack_signature ----

def test_verify_signature_happy_path():
    body = b'{"x":1}'
    ts, sig = _sign(body)
    verify_slack_signature(SIGNING_SECRET, ts, sig, body)


def test_verify_signature_bad_sig():
    body = b'{"x":1}'
    ts, sig = _sign(body)
    with pytest.raises(SignatureError, match="mismatch"):
        verify_slack_signature(SIGNING_SECRET, ts, sig[:-1] + ("0" if sig[-1] != "0" else "1"), body)


def test_verify_signature_replay_window_expired():
    body = b'{"x":1}'
    old_ts = str(int(time.time()) - 60 * 10)
    _, sig = _sign(body, ts=old_ts)
    with pytest.raises(SignatureError, match="replay window"):
        verify_slack_signature(SIGNING_SECRET, old_ts, sig, body)


def test_verify_signature_missing_headers():
    with pytest.raises(SignatureError, match="missing"):
        verify_slack_signature(SIGNING_SECRET, None, None, b"x")


def test_verify_signature_bad_timestamp():
    with pytest.raises(SignatureError, match="bad timestamp"):
        verify_slack_signature(SIGNING_SECRET, "not-an-int", "v0=x", b"x")


def test_verify_signature_empty_secret():
    with pytest.raises(SignatureError, match="signing secret"):
        verify_slack_signature("", "1", "v0=x", b"x")


# ---- parse_slack_form ----

def test_parse_slack_form():
    body = _form_body({"type": "block_actions", "hello": "world"})
    out = parse_slack_form(body)
    assert out == {"type": "block_actions", "hello": "world"}


def test_parse_slack_form_missing_payload():
    with pytest.raises(ValueError, match="no payload"):
        parse_slack_form(b"other=thing")


# ---- /health ----

def test_health_endpoint():
    app = create_app(signing_secret=SIGNING_SECRET)
    client = app.test_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok", "version": VERSION}


# ---- /slack/interact ----

@pytest.fixture
def mocks():
    slack = MagicMock(spec=SlackHandler)
    # SlackHandler.parse_interaction is a real method we want to execute
    real_handler = SlackHandler(token="t", channel_id="C", client=MagicMock())
    slack.parse_interaction.side_effect = real_handler.parse_interaction
    notion = MagicMock()
    notion.PROPS = {"selected_hook": "Selected Hook"}
    return slack, notion


@pytest.fixture
def client(mocks):
    slack, notion = mocks
    app = create_app(signing_secret=SIGNING_SECRET, slack=slack, notion=notion)
    return app.test_client(), slack, notion


def _post(client_tuple, payload):
    client, _slack, _notion = client_tuple
    body = _form_body(payload)
    ts, sig = _sign(body)
    return client.post(
        "/slack/interact",
        data=body,
        headers={
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )


def test_interact_rejects_missing_signature(client):
    c, _, _ = client
    r = c.post(
        "/slack/interact",
        data=_form_body(_payload(ACTION_APPROVE)),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 401


def test_interact_rejects_bad_signature(client):
    c, _, _ = client
    r = c.post(
        "/slack/interact",
        data=_form_body(_payload(ACTION_APPROVE)),
        headers={
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=deadbeef",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert r.status_code == 401


def test_interact_rejects_stale_timestamp(client):
    c, _, _ = client
    body = _form_body(_payload(ACTION_APPROVE))
    stale_ts = str(int(time.time()) - 60 * 30)
    _, sig = _sign(body, ts=stale_ts)
    r = c.post(
        "/slack/interact",
        data=body,
        headers={
            "X-Slack-Request-Timestamp": stale_ts,
            "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert r.status_code == 401


def test_interact_approve_dispatches_publishing(client):
    c, slack, notion = client
    r = _post(client, _payload(ACTION_APPROVE))
    assert r.status_code == 200
    notion.update_status.assert_called_once_with("page_abc", "Publishing")
    slack.update_card_status.assert_called_once()
    assert "Publishing" in slack.update_card_status.call_args.args[1]


def test_interact_hook_stores_selection(client):
    c, slack, notion = client
    r = _post(client, _payload(f"{ACTION_HOOK_PREFIX}4"))
    assert r.status_code == 200
    notion.save_selected_hook.assert_called_once_with("page_abc", 4)
    slack.update_card_status.assert_called_once()


def test_interact_reject_updates_notion(client):
    c, slack, notion = client
    r = _post(client, _payload(ACTION_REJECT))
    assert r.status_code == 200
    notion.update_status.assert_called_once_with("page_abc", "Rejected")


def test_interact_edit_returns_notion_url(client):
    c, _, _ = client
    r = _post(client, _payload(ACTION_EDIT, value="abc-def-123"))
    assert r.status_code == 200
    body = r.get_json()
    assert "notion.so" in body["text"]
    assert "abcdef123" in body["text"]  # dashes stripped


def test_interact_bad_payload_returns_400(client):
    c, _, _ = client
    body = b"not=a-valid-slack-form"
    ts, sig = _sign(body)
    r = c.post(
        "/slack/interact",
        data=body,
        headers={
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert r.status_code == 400


def test_interact_bad_action_id_returns_400(client):
    c, _, _ = client
    r = _post(client, _payload("nuke_everything"))
    assert r.status_code == 400


def test_interact_dispatch_exception_returns_200_with_warning(client):
    c, _slack, notion = client
    notion.update_status.side_effect = RuntimeError("notion down")
    r = _post(client, _payload(ACTION_APPROVE))
    # Slack must get a 200 within 3s; we log + warn rather than crash
    assert r.status_code == 200
    assert "action failed" in r.get_json()["text"]
