import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, desktop, events, info, tasks  # noqa: E402


def _no_network(request):
    raise httpx.ConnectError("pas de réseau dans les tests", request=request)


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
    # Never monsieur's real A.R.E.S (it may be running on this PC), never the
    # internet: test_ares and test_info bring their own fakes.
    monkeypatch.setattr(config, "ARES", "off")
    monkeypatch.setattr(info, "TRANSPORT", httpx.MockTransport(_no_network))
    monkeypatch.setitem(desktop._launched, "at", None)  # no window "still coming up" from another test
    tasks.TASKS.clear()
    yield
    # A task still running would finish during a later test and publish there
    # (seen on the slower Windows CI): stop it and let its thread end here.
    for task in tasks.running():
        tasks.cancel(task["id"])
    end = time.time() + 10
    while (tasks._active or tasks._waiting) and time.time() < end:
        time.sleep(0.02)
    tasks.TASKS.clear()


@pytest.fixture
def published(monkeypatch):
    """Events pushed to the page during the test."""
    seen = []
    monkeypatch.setattr(events, "publish", lambda kind, data: seen.append({"type": kind, **data}))
    return seen
