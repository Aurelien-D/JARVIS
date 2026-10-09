import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, events, tasks  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def never_the_real_data(tmp_path_factory):
    """Not monsieur's data/ nor his OpenAI key, even between tests: a task thread
    that ends after its test writes to the DATA_DIR restored by monkeypatch."""
    config.DATA_DIR = tmp_path_factory.mktemp("data")
    config.OPENAI_API_KEY = "sk-fake"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Each test: its own empty data folder, no briefing, no leftover tasks."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "BRIEFING_TIME", "")
    tasks.TASKS.clear()
    yield
    tasks.TASKS.clear()


@pytest.fixture
def published(monkeypatch):
    """Events pushed to the page during the test."""
    seen = []
    monkeypatch.setattr(events, "publish", lambda kind, data: seen.append({"type": kind, **data}))
    return seen
