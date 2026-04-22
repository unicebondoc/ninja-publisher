import base64
import json

import pytest
import responses

from services.image_gen import (
    BRAND_PROMPT,
    MINIMAX_ENDPOINT,
    build_prompt,
    generate_image,
)


def test_build_prompt_injects_subject():
    out = build_prompt("AI butler on Hetzner")
    assert "AI butler on Hetzner" in out
    assert "bioluminescent" in out
    assert "no text" in out


def test_brand_prompt_has_required_keywords():
    for keyword in ("purple", "teal", "Filipino", "editorial", "no text"):
        assert keyword in BRAND_PROMPT


def test_generate_requires_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MINIMAX_API_KEY"):
        generate_image("x")


@responses.activate
def test_generate_image_happy_path():
    fake_png = b"\x89PNG\r\n\x1a\nFAKEPIXELS"
    b64 = base64.b64encode(fake_png).decode()
    responses.add(
        responses.POST,
        MINIMAX_ENDPOINT,
        json={"data": {"image_base64": [b64]}},
        status=200,
    )
    out = generate_image("a subject", api_key="test-key")
    assert out == fake_png

    call = responses.calls[0].request
    assert call.headers["Authorization"] == "Bearer test-key"
    body = json.loads(call.body)
    assert body["model"] == "image-01"
    assert body["aspect_ratio"] == "16:9"
    assert body["response_format"] == "base64"
    assert "a subject" in body["prompt"]


@responses.activate
def test_generate_image_respects_aspect_ratio():
    b64 = base64.b64encode(b"x").decode()
    responses.add(responses.POST, MINIMAX_ENDPOINT,
                  json={"data": {"image_base64": [b64]}}, status=200)
    generate_image("s", aspect_ratio="1:1", api_key="k")
    body = json.loads(responses.calls[0].request.body)
    assert body["aspect_ratio"] == "1:1"


@responses.activate
def test_generate_image_raises_on_http_error():
    import requests as _requests
    responses.add(responses.POST, MINIMAX_ENDPOINT, status=500, json={"error": "boom"})
    with pytest.raises(_requests.HTTPError):
        generate_image("s", api_key="k")


@responses.activate
def test_generate_image_raises_on_empty_payload():
    responses.add(responses.POST, MINIMAX_ENDPOINT, status=200, json={"data": {}})
    with pytest.raises(RuntimeError, match="image_base64"):
        generate_image("s", api_key="k")
