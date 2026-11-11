"""Logfire: a span per request with the app's logs inside it; tests never send."""

import json
from pathlib import Path

import logfire
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from london_brent_lewisham_chat import app as app_module
from london_brent_lewisham_chat import observability
from london_brent_lewisham_chat.app import create_app
from london_brent_lewisham_chat.config import Settings
from london_brent_lewisham_chat.observability import configure_logfire

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


def test_environment_defaults_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert Settings().environment == "local"


def test_environment_is_read_from_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert Settings().environment == "production"


def test_an_unknown_environment_stops_the_app_at_start_up() -> None:
    with pytest.raises(ValidationError, match="environment"):
        Settings(environment="prod")  # type: ignore[arg-type]


def start_up_lines(out: str) -> list[dict]:
    """Return the "app started" lines from captured stdout, each parsed from JSON."""
    lines = [json.loads(line) for line in out.splitlines()]
    return [line for line in lines if line["message"] == "app started"]


def test_start_up_logs_the_environment_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with TestClient(create_app(Settings(environment="production"))) as client:
        client.get("/health")

    [line] = start_up_lines(capsys.readouterr().out)
    assert line["environment"] == "production"
    assert line["sending_to_logfire"] is False


def test_start_up_says_traces_are_sent_but_never_logs_the_token(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Configure Logfire as if there were no token, so this test sends nothing;
    # the app's settings still hold the token, which is what the log line reports.
    monkeypatch.setattr(
        app_module,
        "configure_logfire",
        lambda settings: configure_logfire(
            settings.model_copy(update={"logfire_token": None})
        ),
    )
    with TestClient(create_app(Settings(logfire_token="secret-value"))):
        pass

    out = capsys.readouterr().out
    assert "secret-value" not in out
    [line] = start_up_lines(out)
    assert line["sending_to_logfire"] is True


@pytest.mark.parametrize(
    ("key", "value", "kept"),
    [
        ("session_id", "demo-1", True),  # matches "session", but is not a secret
        ("local_authority", "Brent", True),  # matches "auth", but is not a secret
        ("session_cookie", "c00kie", False),  # same pattern, not on the allow-list
        ("auth_token", "t0k3n", False),
        ("password", "hunter2", False),
        ("api_key", "sk-abc", False),
    ],
)
def test_scrubbing_lets_through_only_the_allow_listed_keys(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str, kept: bool
) -> None:
    exporter = InMemorySpanExporter()
    configure = logfire.configure

    def with_exporter(**options: object) -> None:
        # Our configuration, plus an in-memory exporter to read the span back.
        configure(
            **options,  # type: ignore[arg-type]
            additional_span_processors=[SimpleSpanProcessor(exporter)],
        )

    monkeypatch.setattr(observability.logfire, "configure", with_exporter)
    configure_logfire(Settings())
    with logfire.span("check", **{key: value}):
        pass

    [span] = [s for s in exporter.get_finished_spans() if s.name == "check"]
    shown = span.attributes[key]
    assert (shown == value) is kept
    if not kept:
        assert str(shown).startswith("[Scrubbed due to")


def test_scrubbing_reaches_inside_tool_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    configure = logfire.configure
    monkeypatch.setattr(
        observability.logfire,
        "configure",
        lambda **o: configure(
            **o, additional_span_processors=[SimpleSpanProcessor(exporter)]
        ),
    )
    configure_logfire(Settings())
    with logfire.span("tool", args={"local_authority": "Brent", "password": "hunter2"}):
        pass

    [span] = [s for s in exporter.get_finished_spans() if s.name == "tool"]
    args = json.loads(span.attributes["args"])
    assert args["local_authority"] == "Brent"
    assert args["password"].startswith("[Scrubbed due to")
