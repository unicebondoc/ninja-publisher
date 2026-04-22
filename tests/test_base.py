import pytest

from base import Article, BasePublisher, PublishError, PublishResult


def test_article_defaults():
    a = Article(title="T", slug="t", body_markdown="# body")
    assert a.tags == []
    assert a.subtitle is None
    assert a.canonical_url is None
    assert a.extra == {}


def test_publish_result_roundtrip():
    r = PublishResult(platform="medium", url="https://m.co/x", id="abc", raw={"k": 1})
    assert r.platform == "medium"
    assert r.url == "https://m.co/x"
    assert r.id == "abc"
    assert r.raw == {"k": 1}


def test_publish_error_carries_context():
    err = PublishError("medium", "boom", status=500, raw={"e": 1})
    assert err.platform == "medium"
    assert err.status == 500
    assert err.raw == {"e": 1}
    assert "[medium] boom" in str(err)


def test_base_publisher_is_abstract():
    with pytest.raises(TypeError):
        BasePublisher()  # type: ignore[abstract]


def test_concrete_publisher_satisfies_abc():
    class Dummy(BasePublisher):
        platform = "dummy"

        def publish(self, article, images):
            return PublishResult(platform=self.platform, url="https://x/y")

    d = Dummy()
    out = d.publish(Article(title="T", slug="t", body_markdown="x"), [])
    assert out.url == "https://x/y"
