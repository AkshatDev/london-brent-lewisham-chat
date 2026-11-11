"""Fixtures shared by all tests: the FastAPI app and a test client for it."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from london_brent_lewisham_chat.app import create_app
from london_brent_lewisham_chat.config import Settings


@pytest.fixture(scope="session", autouse=True)
def keep_tests_offline() -> Iterator[None]:
    """Keep every test offline: no Logfire token, OpenAI key or Datahub header.

    None of them is read from ``.env`` or the shell. Without a token nothing is sent
    to Logfire; without a key or header the app builds no OpenAI or Datahub client,
    so no test can make a real (billed or rate-limited) call. Tests that need those
    services put a fake client on ``app.state`` themselves.

    Session-scoped so it is in place before the module-scoped ``app`` below and
    before any ``Settings()`` a test builds itself.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("LOGFIRE_TOKEN", raising=False)
        mp.delenv("OPENAI_API_KEY", raising=False)
        mp.delenv("DATAHUB_ALLOW_REQUEST", raising=False)
        mp.setitem(Settings.model_config, "env_file", None)
        yield


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """Create one FastAPI app per test module, with fixed settings."""
    return create_app(Settings(log_level="INFO"))


@pytest.fixture(scope="module")
def client(app: FastAPI) -> Iterator[TestClient]:
    """Create a test client; ``with`` runs the app's lifespan around the tests."""
    with TestClient(app) as client:
        yield client
