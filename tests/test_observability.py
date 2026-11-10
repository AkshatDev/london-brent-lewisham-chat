"""Logfire: a span per request with the app's logs inside it; tests never send."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from london_brent_lewisham_chat.app import create_app
from london_brent_lewisham_chat.config import Settings

# Each test starts its own app while ``capsys`` is active (see test_logging.py).


def test_logfire_console_shows_request_span_with_app_log_nested_inside(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with TestClient(create_app(Settings(logfire_console=True))) as client:
        client.get("/health")

    text = [line for line in capsys.readouterr().out.splitlines() if line[:1] != "{"]
    span = next(i for i, line in enumerate(text) if line.endswith(" GET /health"))
    # Indented under the span = the log record belongs to that request's trace.
    assert text[span + 1].endswith("   request finished")


def test_logfire_prints_nothing_by_default_so_stdout_stays_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with TestClient(create_app(Settings(logfire_console=False))) as client:
        client.get("/health")

    lines = capsys.readouterr().out.splitlines()
    assert lines
    for line in lines:
        json.loads(line)  # raises if Logfire printed any text line


def test_tests_never_read_a_logfire_token_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("LOGFIRE_TOKEN=would-send\n")
    monkeypatch.chdir(tmp_path)
    assert Settings().logfire_token is None


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_blank_logfire_token_means_no_token(blank: str) -> None:
    assert Settings(logfire_token=blank).logfire_token is None


def test_the_logfire_token_is_hidden_when_settings_are_printed() -> None:
    settings = Settings(logfire_token="secret-value")
    assert "secret-value" not in repr(settings)
    assert settings.logfire_token is not None
    assert settings.logfire_token.get_secret_value() == "secret-value"
