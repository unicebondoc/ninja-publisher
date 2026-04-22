from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Article:
    title: str
    slug: str
    body_markdown: str
    tags: list[str] = field(default_factory=list)
    subtitle: str | None = None
    linkedin_hook: str | None = None
    notion_page_id: str | None = None
    canonical_url: str | None = None
    hero_image_url: str | None = None
    hook_options: list[str] = field(default_factory=list)
    selected_hook: int | None = None
    word_count: int | None = None
    reading_time_minutes: int | None = None
    publish_to: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResult:
    platform: str
    url: str
    id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PublishError(RuntimeError):
    def __init__(self, platform: str, message: str, *, status: int | None = None, raw: Any = None):
        super().__init__(f"[{platform}] {message}")
        self.platform = platform
        self.status = status
        self.raw = raw


class BasePublisher(ABC):
    platform: str = ""

    @abstractmethod
    def publish(self, article: Article, images: list[bytes]) -> PublishResult:
        ...
