"""Notion wrapper for ninja-publisher.

Schema contract (DB will be created by boss in parallel — field names below are
the agreed keys; all reads/writes go through NotionClient.PROPS so a rename only
touches one place).
"""

from __future__ import annotations

import json
import os
from typing import Any

from notion_client import Client

from base import Article


class NotionError(RuntimeError):
    pass


class NotionClient:
    PROPS = {
        "title": "Title",
        "slug": "Slug",
        "status": "Status",
        "body": "Body",
        "tags": "Tags",
        "subtitle": "Subtitle",
        "hero_image_url": "Hero Image URL",
        "hook_options": "Hook Options",
        "selected_hook": "Selected Hook",
        "word_count": "Word Count",
        "reading_time": "Reading Time",
        "publish_to": "Publish To",
        "canonical_url": "Canonical URL",
        "last_error": "Last Error",
    }
    PLATFORM_URL_PROPS = {
        "medium": "Medium URL",
        "linkedin": "LinkedIn URL",
    }
    PLATFORM_STATS_PROPS = {
        "medium": "Medium Stats",
        "linkedin": "LinkedIn Stats",
    }

    def __init__(
        self,
        token: str | None = None,
        db_id: str | None = None,
        *,
        client: Any = None,
    ):
        self.token = token or os.environ.get("NOTION_TOKEN")
        self.db_id = db_id or os.environ.get("NOTION_DB_ID")
        if not self.token:
            raise NotionError("NOTION_TOKEN is not set")
        if not self.db_id:
            raise NotionError("NOTION_DB_ID is not set")
        self._client = client or Client(auth=self.token)

    # ---- queries ----

    def query_rows_by_status(self, status: str) -> list[Article]:
        resp = self._client.databases.query(
            database_id=self.db_id,
            filter={
                "property": self.PROPS["status"],
                "select": {"equals": status},
            },
        )
        return [self._article_from_page(p) for p in resp.get("results", [])]

    # ---- writes ----

    def save_draft(self, article: Article) -> str:
        props = self._article_to_props(article, include_status=True)
        page = self._client.pages.create(
            parent={"database_id": self.db_id},
            properties=props,
        )
        page_id = page.get("id")
        if not page_id:
            raise NotionError(f"pages.create returned no id: {page}")
        return page_id

    def update_status(self, page_id: str, status: str) -> None:
        self._client.pages.update(
            page_id=page_id,
            properties={
                self.PROPS["status"]: {"select": {"name": status}},
            },
        )

    def save_platform_url(self, page_id: str, platform: str, url: str) -> None:
        prop = self.PLATFORM_URL_PROPS.get(platform)
        if not prop:
            raise NotionError(f"no URL column configured for platform {platform!r}")
        self._client.pages.update(
            page_id=page_id,
            properties={prop: {"url": url}},
        )

    def save_stats(self, page_id: str, platform: str, stats: dict) -> None:
        prop = self.PLATFORM_STATS_PROPS.get(platform)
        if not prop:
            raise NotionError(f"no stats column configured for platform {platform!r}")
        self._client.pages.update(
            page_id=page_id,
            properties={prop: _rich_text(json.dumps(stats, sort_keys=True))},
        )

    def log_error(self, page_id: str, platform: str, error: str) -> None:
        stamped = f"[{platform}] {error}"
        self._client.pages.update(
            page_id=page_id,
            properties={
                self.PROPS["last_error"]: _rich_text(stamped[:2000]),
                self.PROPS["status"]: {"select": {"name": "Errored"}},
            },
        )

    # ---- mapping ----

    def _article_to_props(self, article: Article, *, include_status: bool) -> dict:
        props: dict[str, Any] = {
            self.PROPS["title"]: {
                "title": [{"type": "text", "text": {"content": article.title}}]
            },
            self.PROPS["slug"]: _rich_text(article.slug),
            self.PROPS["body"]: _rich_text(article.body_markdown),
            self.PROPS["tags"]: {
                "multi_select": [{"name": t} for t in article.tags[:5]]
            },
            self.PROPS["publish_to"]: {
                "multi_select": [{"name": p} for p in article.publish_to]
            },
        }
        if article.subtitle:
            props[self.PROPS["subtitle"]] = _rich_text(article.subtitle)
        if article.hero_image_url:
            props[self.PROPS["hero_image_url"]] = {"url": article.hero_image_url}
        if article.hook_options:
            props[self.PROPS["hook_options"]] = _rich_text(
                "\n".join(article.hook_options)
            )
        if article.selected_hook is not None:
            props[self.PROPS["selected_hook"]] = {"number": article.selected_hook}
        if article.word_count is not None:
            props[self.PROPS["word_count"]] = {"number": article.word_count}
        if article.reading_time_minutes is not None:
            props[self.PROPS["reading_time"]] = {"number": article.reading_time_minutes}
        if article.canonical_url:
            props[self.PROPS["canonical_url"]] = {"url": article.canonical_url}
        if include_status:
            props[self.PROPS["status"]] = {"select": {"name": "Draft"}}
        return props

    def _article_from_page(self, page: dict) -> Article:
        props = page.get("properties") or {}
        title = _read_title(props.get(self.PROPS["title"], {}))
        slug = _read_plain_text(props.get(self.PROPS["slug"], {}))
        body = _read_plain_text(props.get(self.PROPS["body"], {}))
        subtitle = _read_plain_text(props.get(self.PROPS["subtitle"], {})) or None
        hero = _read_url(props.get(self.PROPS["hero_image_url"], {}))
        hooks_raw = _read_plain_text(props.get(self.PROPS["hook_options"], {}))
        hook_options = [h for h in hooks_raw.split("\n") if h] if hooks_raw else []
        selected = _read_number(props.get(self.PROPS["selected_hook"], {}))
        word_count = _read_number(props.get(self.PROPS["word_count"], {}))
        reading = _read_number(props.get(self.PROPS["reading_time"], {}))
        tags = _read_multi_select(props.get(self.PROPS["tags"], {}))
        publish_to = _read_multi_select(props.get(self.PROPS["publish_to"], {}))
        canonical = _read_url(props.get(self.PROPS["canonical_url"], {}))
        return Article(
            title=title,
            slug=slug,
            body_markdown=body,
            subtitle=subtitle,
            tags=tags,
            hero_image_url=hero,
            hook_options=hook_options,
            selected_hook=int(selected) if selected is not None else None,
            word_count=int(word_count) if word_count is not None else None,
            reading_time_minutes=int(reading) if reading is not None else None,
            publish_to=publish_to,
            canonical_url=canonical,
            notion_page_id=page.get("id"),
        )


def _rich_text(text: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}


def _read_title(prop: dict) -> str:
    parts = prop.get("title") or []
    return "".join(p.get("plain_text", "") for p in parts)


def _read_plain_text(prop: dict) -> str:
    parts = prop.get("rich_text") or []
    return "".join(p.get("plain_text", "") for p in parts)


def _read_url(prop: dict) -> str | None:
    return prop.get("url")


def _read_number(prop: dict) -> float | None:
    return prop.get("number")


def _read_multi_select(prop: dict) -> list[str]:
    return [item.get("name", "") for item in prop.get("multi_select") or []]
