import logging
import os
from typing import Any

import requests

from base import Article, BasePublisher, PublishError, PublishResult

MEDIUM_API = "https://api.medium.com/v1"
DEFAULT_CANONICAL_BASE = "https://unicebondoc.com/blog"

log = logging.getLogger("medium")


class MediumPublisher(BasePublisher):
    platform = "medium"

    def __init__(
        self,
        token: str | None = None,
        canonical_base: str | None = None,
        session: requests.Session | None = None,
        dry_run: bool = False,
    ):
        self.dry_run = dry_run
        self.token = token or os.environ.get("MEDIUM_TOKEN")
        if not self.token:
            raise PublishError(self.platform, "MEDIUM_TOKEN is not set")
        self.canonical_base = (
            canonical_base
            or os.environ.get("CANONICAL_BASE_URL")
            or DEFAULT_CANONICAL_BASE
        ).rstrip("/")
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
        }

    def _get_user_id(self) -> str:
        r = self.session.get(f"{MEDIUM_API}/me", headers=self._headers(), timeout=30)
        if r.status_code != 200:
            raise PublishError(
                self.platform,
                f"failed to resolve Medium user: HTTP {r.status_code}",
                status=r.status_code,
                raw=_safe_json(r),
            )
        data = _safe_json(r)
        user_id = (data.get("data") or {}).get("id")
        if not user_id:
            raise PublishError(self.platform, "Medium /me response missing data.id", raw=data)
        return user_id

    def canonical_url_for(self, article: Article) -> str:
        return article.canonical_url or f"{self.canonical_base}/{article.slug}"

    def publish(self, article: Article, images: list[bytes]) -> PublishResult:
        if self.dry_run:
            log.info("[DRY RUN] Would publish to Medium:")
            log.info("  Title:       %s", article.title)
            log.info("  Tags:        %s", article.tags[:5])
            log.info("  Canonical:   %s", self.canonical_url_for(article))
            log.info("  Body preview: %s", article.body_markdown[:500])
            log.info("  API calls that would be made:")
            log.info("    GET  %s/me", MEDIUM_API)
            log.info("    POST %s/users/{user_id}/posts", MEDIUM_API)
            return PublishResult(
                platform=self.platform,
                url="dry-run",
                id="dry-run",
                raw={"dry_run": True, "title": article.title, "tags": article.tags[:5]},
            )
        user_id = self._get_user_id()
        canonical = self.canonical_url_for(article)
        payload: dict[str, Any] = {
            "title": article.title,
            "contentFormat": "markdown",
            "content": article.body_markdown,
            "tags": article.tags[:5],
            "canonicalUrl": canonical,
            "publishStatus": "public",
        }
        r = self.session.post(
            f"{MEDIUM_API}/users/{user_id}/posts",
            headers=self._headers(),
            json=payload,
            timeout=60,
        )
        if r.status_code not in (200, 201):
            raise PublishError(
                self.platform,
                f"publish failed: HTTP {r.status_code}",
                status=r.status_code,
                raw=_safe_json(r),
            )
        data = _safe_json(r).get("data") or {}
        url = data.get("url")
        if not url:
            raise PublishError(self.platform, "Medium response missing data.url", raw=data)
        return PublishResult(
            platform=self.platform,
            url=url,
            id=data.get("id"),
            raw=data,
        )


def _safe_json(r: requests.Response) -> dict:
    try:
        return r.json()
    except ValueError:
        return {"_non_json": r.text[:500]}
