"""Tests for request logging: one JSON log line per request, including failures."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from london_brent_lewisham_chat.app import create_app
from london_brent_lewisham_chat.config import Settings

APP_LOGGER = "london_brent_lewisham_chat.app"


def request_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    """Return the app's per-request log lines from stdout, each parsed from JSON.

    Only the request lines: other app logs (e.g. "app started") are not what these
    tests check, and must not change their counts.
    """
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return [
        line
        for line in lines
        if line["name"] == APP_LOGGER
        and line["message"] in {"request finished", "request failed"}
    ]


# Each test starts its own app: the log handler keeps the stdout that existed when
# logging was configured, so the app must start while ``capsys`` is active.


def test_request_is_logged_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/health")

    [line] = request_lines(capsys)
    assert line["levelname"] == "INFO"
    assert line["method"] == "GET"
    assert line["path"] == "/health"
    assert line["status_code"] == 200
    assert isinstance(line["duration_ms"], float)
    assert line["correlation_id"] == response.headers["X-Request-ID"]
    assert line["time"].endswith("+00:00")  # ISO 8601, UTC


def test_unhandled_error_is_logged_and_returns_500_with_request_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = create_app(Settings())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(app) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert response.headers["X-Request-ID"]

    [line] = request_lines(capsys)
    assert line["levelname"] == "ERROR"
    assert line["status_code"] == 500
    assert line["path"] == "/boom"
    assert "RuntimeError: boom" in line["exc_info"]
    assert line["correlation_id"] == response.headers["X-Request-ID"]
