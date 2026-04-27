"""Tests for services/article_drafter.py — Claude CLI article drafter."""

import subprocess
from unittest.mock import patch

import pytest

from base import Article
from services.article_drafter import ArticleDrafter, DraftError

SAMPLE_OUTPUT = """\
TITLE: Why I Let an AI Butler Run My Server
SLUG: why-i-let-an-ai-butler-run-my-server
TAGS: AI, automation, personal-essay, coding, butler
---
I have a confession to make.

Last Tuesday, I gave an AI agent root access to my Hetzner VPS.
And I slept like a baby.

## The Setup

Here is the rest of the article body in markdown.
More paragraphs would follow here.
"""


def _drafter(**kwargs) -> ArticleDrafter:
    return ArticleDrafter(**kwargs)


def _mock_run_success(output: str = SAMPLE_OUTPUT):
    """Return a mock CompletedProcess with the given stdout."""
    return subprocess.CompletedProcess(
        args=["claude", "--print"],
        returncode=0,
        stdout=output,
        stderr="",
    )


# ---- happy path ----


def test_draft_parses_output():
    drafter = _drafter()
    with patch("services.article_drafter.subprocess.run", return_value=_mock_run_success()):
        article = drafter.draft("AI butler servers")

    assert isinstance(article, Article)
    assert article.title == "Why I Let an AI Butler Run My Server"
    assert article.slug == "why-i-let-an-ai-butler-run-my-server"
    assert article.tags == ["AI", "automation", "personal-essay", "coding", "butler"]
    assert "confession" in article.body_markdown
    assert "## The Setup" in article.body_markdown
    assert article.word_count is not None
    assert article.word_count > 0
    assert article.reading_time_minutes is not None
    assert article.reading_time_minutes >= 1


def test_draft_sends_prompt_via_stdin():
    drafter = _drafter()
    with patch("services.article_drafter.subprocess.run", return_value=_mock_run_success()) as mock:
        drafter.draft("test topic")

    call_kwargs = mock.call_args
    assert call_kwargs.kwargs["input"] is not None
    assert "test topic" in call_kwargs.kwargs["input"]


def test_draft_uses_configured_model():
    drafter = _drafter(model="claude-sonnet-4-20250514")
    with patch("services.article_drafter.subprocess.run", return_value=_mock_run_success()) as mock:
        drafter.draft("test topic")

    cmd = mock.call_args.args[0]
    assert "claude-sonnet-4-20250514" in cmd


# ---- voice profile ----


def test_voice_profile_in_prompt():
    """The prompt sent to claude contains voice profile keywords."""
    drafter = _drafter()
    with patch("services.article_drafter.subprocess.run", return_value=_mock_run_success()) as mock:
        drafter.draft("any topic")

    prompt_sent = mock.call_args.kwargs["input"]
    assert "Unice Bondoc" in prompt_sent
    assert "Filipino-Australian" in prompt_sent
    assert "conversational" in prompt_sent
    assert "bioluminescent" in prompt_sent
    assert "ghostwriting" in prompt_sent


# ---- CLI failure ----


def test_draft_cli_failure():
    drafter = _drafter()
    failed = subprocess.CompletedProcess(
        args=["claude"], returncode=1, stdout="", stderr="Error: something went wrong"
    )
    with (
        patch("services.article_drafter.subprocess.run", return_value=failed),
        pytest.raises(DraftError, match="exited with code 1"),
    ):
        drafter.draft("test topic")


def test_draft_cli_not_found():
    drafter = _drafter()
    with (
        patch(
            "services.article_drafter.subprocess.run",
            side_effect=FileNotFoundError("No such file"),
        ),
        pytest.raises(DraftError, match="claude CLI not found"),
    ):
        drafter.draft("test topic")


# ---- timeout ----


def test_draft_timeout():
    drafter = _drafter(timeout=5)
    with (
        patch(
            "services.article_drafter.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5),
        ),
        pytest.raises(DraftError, match="timed out"),
    ):
        drafter.draft("test topic")


# ---- malformed output ----


def test_draft_malformed_no_title():
    output = "SLUG: foo\nTAGS: a, b\n---\nBody text here."
    drafter = _drafter()
    with (
        patch(
            "services.article_drafter.subprocess.run",
            return_value=_mock_run_success(output),
        ),
        pytest.raises(DraftError, match="TITLE"),
    ):
        drafter.draft("test")


def test_draft_malformed_no_slug():
    output = "TITLE: Foo\nTAGS: a, b\n---\nBody text here."
    drafter = _drafter()
    with (
        patch(
            "services.article_drafter.subprocess.run",
            return_value=_mock_run_success(output),
        ),
        pytest.raises(DraftError, match="SLUG"),
    ):
        drafter.draft("test")


def test_draft_malformed_no_tags():
    output = "TITLE: Foo\nSLUG: foo\n---\nBody text here."
    drafter = _drafter()
    with (
        patch(
            "services.article_drafter.subprocess.run",
            return_value=_mock_run_success(output),
        ),
        pytest.raises(DraftError, match="TAGS"),
    ):
        drafter.draft("test")


def test_draft_malformed_no_separator():
    output = "TITLE: Foo\nSLUG: foo\nTAGS: a, b\nBody without separator."
    drafter = _drafter()
    with (
        patch(
            "services.article_drafter.subprocess.run",
            return_value=_mock_run_success(output),
        ),
        pytest.raises(DraftError, match="separator"),
    ):
        drafter.draft("test")


def test_draft_malformed_empty_body():
    output = "TITLE: Foo\nSLUG: foo\nTAGS: a, b\n---\n   \n  \n"
    drafter = _drafter()
    with (
        patch(
            "services.article_drafter.subprocess.run",
            return_value=_mock_run_success(output),
        ),
        pytest.raises(DraftError, match="body is empty"),
    ):
        drafter.draft("test")


def test_draft_empty_topic():
    drafter = _drafter()
    with pytest.raises(DraftError, match="topic must not be empty"):
        drafter.draft("")


def test_draft_empty_output():
    empty = subprocess.CompletedProcess(args=["claude"], returncode=0, stdout="", stderr="")
    drafter = _drafter()
    with (
        patch("services.article_drafter.subprocess.run", return_value=empty),
        pytest.raises(DraftError, match="empty output"),
    ):
        drafter.draft("test")
