import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "MEDIUM_TOKEN",
        "MINIMAX_API_KEY",
        "CANONICAL_BASE_URL",
        "NOTION_TOKEN",
        "SLACK_BOT_TOKEN",
        "POSTIZ_API_KEY",
        "POSTIZ_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
