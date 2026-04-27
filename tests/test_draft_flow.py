"""Tests for the Telegram-triggered article drafting flow in approval_server.py."""

from unittest.mock import MagicMock, patch

from approval_server import handle_draft_request
from base import Article
from services.article_drafter import DraftError

PAGE_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
CHAT_ID = "7522106491"


def _article(**overrides) -> Article:
    defaults = {
        "title": "Test Draft Article",
        "slug": "test-draft-article",
        "body_markdown": "Some body content here.",
        "tags": ["AI", "python"],
        "word_count": 200,
        "reading_time_minutes": 1,
    }
    defaults.update(overrides)
    return Article(**defaults)


def _mock_deps():
    """Create mock dependencies for handle_draft_request."""
    notion = MagicMock()
    notion.save_draft.return_value = PAGE_ID
    tg_bot = MagicMock()
    tg_bot.send_message.return_value = {"message_id": 500}
    drafter = MagicMock()
    drafter.draft.return_value = _article()
    return notion, tg_bot, drafter


# ---- happy path ----


@patch("approval_server.generate_image")
def test_draft_request_happy_path(mock_gen_image):
    """Full flow: draft -> image -> notion -> approval card."""
    mock_gen_image.return_value = b"fake-image-bytes"
    notion, tg_bot, drafter = _mock_deps()

    handle_draft_request(notion, tg_bot, drafter, CHAT_ID, "AI agents", 100)

    # Drafter called with topic
    drafter.draft.assert_called_once_with("AI agents")

    # Status messages sent
    tg_bot.send_message.assert_called_once()
    assert "AI agents" in tg_bot.send_message.call_args.args[1]

    # Edit messages for status updates
    assert tg_bot.edit_message.call_count >= 1

    # Notion save_draft + update_status called
    notion.save_draft.assert_called_once()
    notion.update_status.assert_called_once_with(PAGE_ID, "Ready")

    # Approval card sent
    tg_bot.send_approval_card.assert_called_once()
    card_article = tg_bot.send_approval_card.call_args.args[0]
    assert card_article.notion_page_id == PAGE_ID


@patch("approval_server.generate_image")
def test_draft_request_image_failure_continues(mock_gen_image):
    """If hero image generation fails, the article is still saved and approval sent."""
    mock_gen_image.side_effect = RuntimeError("MiniMax down")
    notion, tg_bot, drafter = _mock_deps()

    handle_draft_request(notion, tg_bot, drafter, CHAT_ID, "AI agents", 100)

    # Draft still proceeds
    drafter.draft.assert_called_once()
    notion.save_draft.assert_called_once()
    notion.update_status.assert_called_once_with(PAGE_ID, "Ready")
    tg_bot.send_approval_card.assert_called_once()


@patch("approval_server.generate_image")
def test_draft_request_drafter_failure(mock_gen_image):
    """If drafting fails, error message sent to Telegram."""
    notion, tg_bot, drafter = _mock_deps()
    drafter.draft.side_effect = DraftError("claude CLI not found")

    handle_draft_request(notion, tg_bot, drafter, CHAT_ID, "AI agents", 100)

    # Approval card NOT sent
    tg_bot.send_approval_card.assert_not_called()
    # Notion NOT called
    notion.save_draft.assert_not_called()
    # Error message sent to user (second call to send_message)
    assert tg_bot.send_message.call_count == 2
    error_call = tg_bot.send_message.call_args_list[-1]
    assert "Draft failed" in error_call.args[1]


@patch("approval_server.generate_image")
def test_draft_request_notion_failure(mock_gen_image):
    """If Notion save fails, error message sent to Telegram."""
    mock_gen_image.return_value = b"fake-image"
    notion, tg_bot, drafter = _mock_deps()
    notion.save_draft.side_effect = RuntimeError("Notion API error")

    handle_draft_request(notion, tg_bot, drafter, CHAT_ID, "AI agents", 100)

    # Approval card NOT sent
    tg_bot.send_approval_card.assert_not_called()
    # Error message sent
    error_call = tg_bot.send_message.call_args_list[-1]
    assert "Draft failed" in error_call.args[1]


@patch("approval_server.generate_image")
def test_draft_request_truncates_long_topic(mock_gen_image):
    """Status message truncates very long topics."""
    mock_gen_image.return_value = b"fake-image"
    notion, tg_bot, drafter = _mock_deps()
    long_topic = "x" * 500

    handle_draft_request(notion, tg_bot, drafter, CHAT_ID, long_topic, 100)

    status_text = tg_bot.send_message.call_args_list[0].args[1]
    # The status message should have truncated the topic
    assert len(status_text) < 500
