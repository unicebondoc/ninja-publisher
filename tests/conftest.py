import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ISOLATED_ENV_VARS = (
    "MEDIUM_TOKEN",
    "MINIMAX_API_KEY",
    "CANONICAL_BASE_URL",
    "NOTION_TOKEN",
    "NOTION_DB_ID",
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_CHANNEL_ID",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "POSTIZ_API_KEY",
    "POSTIZ_URL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ISOLATED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
