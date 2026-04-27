"""Article drafter — shells out to Claude CLI to ghostwrite articles in Unice's voice."""

from __future__ import annotations

import logging
import re
import subprocess

from base import Article

log = logging.getLogger("article_drafter")

VOICE_PROMPT = """\
You are ghostwriting a Medium article for Unice Bondoc.

Voice profile (from his existing articles):
- Personal, conversational, honest. Writes like talking to a friend.
- First person, vulnerable, no corporate polish.
- Starts with a hook or honest confession.
- Mixes personal story with the technical thing he built.
- Self-aware humor -- knows when something sounds absurd, leans into it.
- Philosophical undercurrent -- even tech articles connect to deeper "why."
- Short punchy paragraphs, lots of whitespace.
- Titles are attention-grabbing, slightly provocative.
- Range: tech/AI builds AND personal essays. Best ones blend both.

Background: Unice is a Filipino-Australian developer in Sydney. He builds AI agents, \
creative apps (What Was Drawn, Quiet Whiskers Oracle), and has a portfolio site that \
looks like a bioluminescent forest. His VPS butler agent runs on a Hetzner server. \
He ships fast and builds in public.

Write a ~800-1000 word Medium article about the topic below.

Output format (STRICT -- follow exactly):
TITLE: <article title>
SLUG: <url-slug-form>
TAGS: <comma-separated, max 5>
---
<article body in markdown>

Topic: {topic}"""

CLI_TIMEOUT_SECONDS = 120


class DraftError(RuntimeError):
    """Raised when article drafting fails."""


class ArticleDrafter:
    """Drafts articles by shelling out to the ``claude`` CLI."""

    def __init__(self, *, model: str = "claude-opus-4-6", timeout: int = CLI_TIMEOUT_SECONDS):
        self._model = model
        self._timeout = timeout

    def draft(self, topic: str) -> Article:
        """Draft an article using Claude CLI and return an Article dataclass."""
        if not topic or not topic.strip():
            raise DraftError("topic must not be empty")

        prompt = VOICE_PROMPT.format(topic=topic.strip())
        raw = self._call_cli(prompt)
        return self._parse_output(raw)

    def _call_cli(self, prompt: str) -> str:
        """Shell out to ``claude --print`` and return stdout."""
        cmd = [
            "claude",
            "--print",
            "--model",
            self._model,
            "--max-turns",
            "1",
        ]
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise DraftError("claude CLI not found -- ensure it is installed and on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise DraftError(f"claude CLI timed out after {self._timeout}s") from exc

        if result.returncode != 0:
            stderr_snippet = (result.stderr or "")[:300]
            raise DraftError(f"claude CLI exited with code {result.returncode}: {stderr_snippet}")

        output = result.stdout.strip()
        if not output:
            raise DraftError("claude CLI returned empty output")

        return output

    def _parse_output(self, raw: str) -> Article:
        """Parse the structured output from Claude into an Article."""
        title_match = re.search(r"^TITLE:\s*(.+)$", raw, re.MULTILINE)
        slug_match = re.search(r"^SLUG:\s*(.+)$", raw, re.MULTILINE)
        tags_match = re.search(r"^TAGS:\s*(.+)$", raw, re.MULTILINE)

        if not title_match:
            raise DraftError("could not parse TITLE from Claude output")
        if not slug_match:
            raise DraftError("could not parse SLUG from Claude output")
        if not tags_match:
            raise DraftError("could not parse TAGS from Claude output")

        title = title_match.group(1).strip()
        slug = slug_match.group(1).strip()
        tags = [t.strip() for t in tags_match.group(1).split(",") if t.strip()][:5]

        # Body is everything after the --- separator
        separator_match = re.search(r"\n---\s*\n?", raw)
        if separator_match is None:
            raise DraftError("could not find --- separator in Claude output")
        body = raw[separator_match.end() :].strip()

        if not body:
            raise DraftError("article body is empty after --- separator")

        # Compute word count and reading time
        word_count = len(body.split())
        reading_time = max(1, round(word_count / 200))

        return Article(
            title=title,
            slug=slug,
            body_markdown=body,
            tags=tags,
            word_count=word_count,
            reading_time_minutes=reading_time,
        )
