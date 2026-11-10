"""Fixtures shared by all tests: the FastAPI app and a test client for it."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from london_brent_lewisham_chat.app import create_app
from london_brent_lewisham_chat.config import Settings


@pytest.fixture(scope="session", autouse=True)
def never_send_to_logfire() -> Iterator[None]:
    """Keep every test offline: no Logfire token from ``.env`` or the shell.

    Session-scoped so it is in place before the module-scoped ``app`` below and
    before any ``Settings()`` a test builds itself.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("LOGFIRE_TOKEN", raising=False)
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
