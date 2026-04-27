import json

import pytest

from base import Article
from services.notion_client import NotionClient, NotionError


class FakeDatabases:
    def __init__(self):
        self.query_calls = []
        self.next_response = {"results": []}

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return self.next_response


class FakePages:
    def __init__(self):
        self.created = []
        self.updated = []
        self.retrieved = []
        self.next_create_response = {"id": "page_new"}
        self.next_retrieve_response: dict | Exception | None = None

    def create(self, **kwargs):
        self.created.append(kwargs)
        return self.next_create_response

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return {"id": kwargs.get("page_id"), "properties": {}}

    def retrieve(self, **kwargs):
        self.retrieved.append(kwargs)
        if isinstance(self.next_retrieve_response, Exception):
            raise self.next_retrieve_response
        if self.next_retrieve_response is not None:
            return self.next_retrieve_response
        return {"id": kwargs.get("page_id"), "properties": {}}


class FakeNotion:
    def __init__(self):
        self.databases = FakeDatabases()
        self.pages = FakePages()


@pytest.fixture
def fake_client():
    return FakeNotion()


@pytest.fixture
def notion(fake_client):
    return NotionClient(token="t", db_id="db", client=fake_client)


def _page(**prop_overrides) -> dict:
    props = {
        "Title": {"title": [{"plain_text": "T"}]},
        "Slug": {"rich_text": [{"plain_text": "t-slug"}]},
        "Body": {"rich_text": [{"plain_text": "# body"}]},
        "Topic": {"multi_select": [{"name": "ai"}, {"name": "ops"}]},
        "Subtitle": {"rich_text": []},
        "Hero Image URL": {"url": "https://cdn.example/h.jpg"},
        "Hook Options": {"rich_text": [{"plain_text": "h1\nh2\nh3"}]},
        "Selected Hook": {"number": 2},
        "Word Count": {"number": 1500},
        "Reading Time": {"number": 7},
        "Publish To": {"multi_select": [{"name": "medium"}, {"name": "linkedin"}]},
        "Canonical URL": {"url": "https://unicebondoc.com/blog/t-slug"},
    }
    props.update(prop_overrides)
    return {"id": "page_1", "properties": props}


def test_requires_token(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setenv("NOTION_DB_ID", "db")
    with pytest.raises(NotionError, match="NOTION_TOKEN"):
        NotionClient(client=FakeNotion())


def test_requires_db_id(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.delenv("NOTION_DB_ID", raising=False)
    with pytest.raises(NotionError, match="NOTION_DB_ID"):
        NotionClient(client=FakeNotion())


def test_query_rows_by_status_passes_filter(notion, fake_client):
    fake_client.databases.next_response = {"results": [_page()]}
    rows = notion.query_rows_by_status("Pending Review")
    assert len(rows) == 1
    call = fake_client.databases.query_calls[0]
    assert call["database_id"] == "db"
    assert call["filter"] == {
        "property": "Status",
        "select": {"equals": "Pending Review"},
    }


def test_query_rows_maps_to_articles(notion, fake_client):
    fake_client.databases.next_response = {"results": [_page(), _page(**{"Slug": {"rich_text": [{"plain_text": "other"}]}})]}
    rows = notion.query_rows_by_status("Pending Review")
    assert [r.slug for r in rows] == ["t-slug", "other"]
    a = rows[0]
    assert isinstance(a, Article)
    assert a.title == "T"
    assert a.tags == ["ai", "ops"]
    assert a.hero_image_url == "https://cdn.example/h.jpg"
    assert a.hook_options == ["h1", "h2", "h3"]
    assert a.selected_hook == 2
    assert a.word_count == 1500
    assert a.reading_time_minutes == 7
    assert a.publish_to == ["medium", "linkedin"]
    assert a.canonical_url == "https://unicebondoc.com/blog/t-slug"
    assert a.notion_page_id == "page_1"


def test_query_rows_handles_empty_results(notion, fake_client):
    fake_client.databases.next_response = {"results": []}
    assert notion.query_rows_by_status("X") == []


def test_save_draft_builds_properties(notion, fake_client):
    article = Article(
        title="Hello",
        slug="hello",
        body_markdown="# hi",
        tags=["a", "b"],
        subtitle="Sub",
        hero_image_url="https://h",
        hook_options=["one", "two"],
        selected_hook=1,
        word_count=100,
        reading_time_minutes=1,
        publish_to=["medium"],
        canonical_url="https://c",
    )
    page_id = notion.save_draft(article)
    assert page_id == "page_new"
    call = fake_client.pages.created[0]
    assert call["parent"] == {"database_id": "db"}
    props = call["properties"]
    assert props["Title"]["title"][0]["text"]["content"] == "Hello"
    assert props["Slug"]["rich_text"][0]["text"]["content"] == "hello"
    assert props["Topic"] == {"multi_select": [{"name": "a"}, {"name": "b"}]}
    assert props["Hero Image URL"] == {"url": "https://h"}
    assert "\n".join(["one", "two"]) in props["Hook Options"]["rich_text"][0]["text"]["content"]
    assert props["Selected Hook"] == {"number": 1}
    assert props["Word Count"] == {"number": 100}
    assert props["Reading Time"] == {"number": 1}
    assert props["Publish To"] == {"multi_select": [{"name": "medium"}]}
    assert props["Status"] == {"select": {"name": "Draft"}}


def test_save_draft_skips_none_fields(notion, fake_client):
    article = Article(title="T", slug="t", body_markdown="b")
    notion.save_draft(article)
    props = fake_client.pages.created[0]["properties"]
    assert "Subtitle" not in props
    assert "Hero Image URL" not in props
    assert "Hook Options" not in props
    assert "Selected Hook" not in props
    assert "Canonical URL" not in props


def test_save_draft_raises_when_no_id_returned(notion, fake_client):
    fake_client.pages.next_create_response = {}
    with pytest.raises(NotionError, match="no id"):
        notion.save_draft(Article(title="T", slug="t", body_markdown="b"))


def test_update_status(notion, fake_client):
    notion.update_status("page_x", "Publishing")
    call = fake_client.pages.updated[0]
    assert call["page_id"] == "page_x"
    assert call["properties"] == {"Status": {"select": {"name": "Publishing"}}}


def test_save_selected_hook(notion, fake_client):
    notion.save_selected_hook("page_x", 7)
    call = fake_client.pages.updated[0]
    assert call["page_id"] == "page_x"
    assert call["properties"] == {"Selected Hook": {"number": 7}}


def test_save_platform_url_medium(notion, fake_client):
    notion.save_platform_url("page_x", "medium", "https://m.co/z")
    call = fake_client.pages.updated[0]
    assert call["properties"] == {"Medium URL": {"url": "https://m.co/z"}}


def test_save_platform_url_linkedin(notion, fake_client):
    notion.save_platform_url("page_x", "linkedin", "https://li/z")
    call = fake_client.pages.updated[0]
    assert call["properties"] == {"LinkedIn URL": {"url": "https://li/z"}}


def test_save_platform_url_unknown_platform(notion):
    with pytest.raises(NotionError, match="twitter"):
        notion.save_platform_url("page_x", "twitter", "https://t/z")


def test_save_stats_serializes_dict(notion, fake_client):
    notion.save_stats("page_x", "medium", {"views": 100, "claps": 42})
    call = fake_client.pages.updated[0]
    written = call["properties"]["Medium Stats"]["rich_text"][0]["text"]["content"]
    assert json.loads(written) == {"claps": 42, "views": 100}


def test_log_error_also_flips_status(notion, fake_client):
    notion.log_error("page_x", "medium", "boom")
    call = fake_client.pages.updated[0]
    props = call["properties"]
    text = props["Last Error"]["rich_text"][0]["text"]["content"]
    assert text == "[medium] boom"
    assert props["Status"] == {"select": {"name": "Errored"}}


def test_log_error_truncates_long_messages(notion, fake_client):
    notion.log_error("page_x", "medium", "x" * 5000)
    written = fake_client.pages.updated[0]["properties"]["Last Error"]["rich_text"][0]["text"]["content"]
    assert len(written) <= 2000


# ---- get_article / get_status (T10-T12) ----


def test_get_article_happy_path(notion, fake_client):
    """T10: get_article returns hydrated Article from pages.retrieve."""
    fake_client.pages.next_retrieve_response = _page()
    article = notion.get_article("page_1")
    assert isinstance(article, Article)
    assert article.title == "T"
    assert article.slug == "t-slug"
    assert article.notion_page_id == "page_1"
    assert article.tags == ["ai", "ops"]
    assert fake_client.pages.retrieved == [{"page_id": "page_1"}]


def test_get_article_missing_page_raises(notion, fake_client):
    """T11: get_article raises ValueError on 404."""
    fake_client.pages.next_retrieve_response = RuntimeError(
        "Could not find page with ID: page_missing. (404)"
    )
    with pytest.raises(ValueError, match="Page not found"):
        notion.get_article("page_missing")


def test_get_status_returns_status_string(notion, fake_client):
    """T12: get_status returns the status select name."""
    fake_client.pages.next_retrieve_response = {
        "id": "page_x",
        "properties": {
            "Status": {"select": {"name": "Publishing"}},
        },
    }
    assert notion.get_status("page_x") == "Publishing"
