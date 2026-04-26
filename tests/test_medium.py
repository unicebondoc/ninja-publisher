import pytest
import responses

from base import Article, PublishError
from publishers.medium import MEDIUM_API, MediumPublisher


def _article(**overrides) -> Article:
    base = dict(
        title="AI Butler on Hetzner",
        slug="ai-butler-on-hetzner",
        body_markdown="# hello\n\nbody",
        tags=["ai", "devops"],
    )
    base.update(overrides)
    return Article(**base)


def test_requires_token(monkeypatch):
    monkeypatch.delenv("MEDIUM_TOKEN", raising=False)
    with pytest.raises(PublishError, match="MEDIUM_TOKEN"):
        MediumPublisher()


def test_canonical_url_default_base():
    pub = MediumPublisher(token="t")
    assert pub.canonical_url_for(_article()) == "https://unicebondoc.com/blog/ai-butler-on-hetzner"


def test_canonical_url_overridden_base():
    pub = MediumPublisher(token="t", canonical_base="https://example.com/posts/")
    assert pub.canonical_url_for(_article()) == "https://example.com/posts/ai-butler-on-hetzner"


def test_canonical_url_env_wins_over_default(monkeypatch):
    monkeypatch.setenv("CANONICAL_BASE_URL", "https://env.example.com/p")
    pub = MediumPublisher(token="t")
    assert pub.canonical_url_for(_article()) == "https://env.example.com/p/ai-butler-on-hetzner"


def test_article_can_override_canonical_url():
    pub = MediumPublisher(token="t")
    art = _article(canonical_url="https://custom.example/one")
    assert pub.canonical_url_for(art) == "https://custom.example/one"


@responses.activate
def test_publish_happy_path():
    responses.add(
        responses.GET,
        f"{MEDIUM_API}/me",
        json={"data": {"id": "user_abc", "username": "unice"}},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{MEDIUM_API}/users/user_abc/posts",
        json={
            "data": {
                "id": "post_123",
                "url": "https://medium.com/@unice/ai-butler-on-hetzner-abcdef",
                "canonicalUrl": "https://unicebondoc.com/blog/ai-butler-on-hetzner",
            }
        },
        status=201,
    )
    pub = MediumPublisher(token="t")
    result = pub.publish(_article(), images=[])
    assert result.platform == "medium"
    assert result.id == "post_123"
    assert result.url.startswith("https://medium.com/")

    publish_call = responses.calls[1]
    assert b'"canonicalUrl": "https://unicebondoc.com/blog/ai-butler-on-hetzner"' in publish_call.request.body
    assert b'"contentFormat": "markdown"' in publish_call.request.body


@responses.activate
def test_publish_tags_capped_at_five():
    responses.add(
        responses.GET, f"{MEDIUM_API}/me",
        json={"data": {"id": "u1"}}, status=200,
    )
    responses.add(
        responses.POST, f"{MEDIUM_API}/users/u1/posts",
        json={"data": {"id": "p", "url": "https://medium.com/x"}}, status=201,
    )
    pub = MediumPublisher(token="t")
    pub.publish(_article(tags=["a", "b", "c", "d", "e", "f", "g"]), images=[])
    body = responses.calls[1].request.body
    assert b'"tags": ["a", "b", "c", "d", "e"]' in body
    assert b'"g"' not in body


@responses.activate
def test_publish_error_on_me_failure():
    responses.add(responses.GET, f"{MEDIUM_API}/me", status=401, json={"errors": [{"message": "bad token"}]})
    pub = MediumPublisher(token="bad")
    with pytest.raises(PublishError) as ei:
        pub.publish(_article(), images=[])
    assert ei.value.status == 401


@responses.activate
def test_publish_error_on_post_failure():
    responses.add(responses.GET, f"{MEDIUM_API}/me", json={"data": {"id": "u1"}}, status=200)
    responses.add(
        responses.POST, f"{MEDIUM_API}/users/u1/posts",
        status=500, json={"errors": [{"message": "boom"}]},
    )
    pub = MediumPublisher(token="t")
    with pytest.raises(PublishError) as ei:
        pub.publish(_article(), images=[])
    assert ei.value.status == 500


@responses.activate
def test_publish_dry_run_no_http_calls():
    pub = MediumPublisher(token="t", dry_run=True)
    result = pub.publish(_article(), images=[])
    assert result.platform == "medium"
    assert result.url == "dry-run"
    assert result.id == "dry-run"
    assert result.raw["dry_run"] is True
    assert len(responses.calls) == 0
