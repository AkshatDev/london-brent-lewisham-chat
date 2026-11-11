"""GET /v1/chat/status: can the service use OpenAI? Never a real OpenAI call.

A fake OpenAI (``httpx2.MockTransport``) is put on ``app.state.openai`` after the
app has started; ``conftest.py`` makes sure no real key is ever loaded.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

from london_brent_lewisham_chat.app import create_app
from london_brent_lewisham_chat.config import Settings

FAKE_KEY = "sk-test-key-that-must-never-appear-1234"
MODEL = "gpt-6-luna"  # the default openai_model

Handler = Callable[[httpx2.Request], httpx2.Response]


def fake_openai(handler: Handler) -> openai.AsyncOpenAI:
    """An OpenAI client whose every request is answered by ``handler``, offline."""
    return openai.AsyncOpenAI(
        api_key=FAKE_KEY,
        max_retries=0,  # one request per check, so handlers see exactly one call
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def call(
    capsys: pytest.CaptureFixture[str],
    handler: Handler | None,
    path: str,
    settings: Settings | None = None,
) -> tuple[Any, list[dict[str, Any]], str]:
    """Start an app (with a fake OpenAI unless ``handler`` is None), GET ``path``.

    Returns:
        The response, the parsed JSON log lines (Logfire's console text lines, if
        any, are left out) and the raw stdout.
    """
    app = create_app(settings or Settings())
    with TestClient(app) as client:
        if handler is not None:
            app.state.openai = fake_openai(handler)
        response = client.get(path)
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return response, lines, out


def model_found(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(
        200, json={"id": MODEL, "object": "model", "created": 0, "owned_by": "openai"}
    )


def key_rejected(request: httpx2.Request) -> httpx2.Response:
    # OpenAI's real 401 text quotes the end of the key: it must not reach logs.
    return httpx2.Response(
        401,
        headers={"x-request-id": "req_test"},
        json={"error": {"message": "Incorrect API key provided: sk-tes****1234"}},
    )


def rate_limited(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(429, json={"error": {"message": "Rate limit reached"}})


def timed_out(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("too slow", request=request)


def unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("no route to host", request=request)


def test_chat_status_is_ok_when_openai_knows_the_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return model_found(request)

    response, _, _ = call(capsys, handler, "/v1/chat/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": MODEL, "detail": None}
    [request] = seen
    assert request.url.path == f"/v1/models/{MODEL}"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"


def test_chat_status_is_503_not_configured_without_a_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response, lines, _ = call(capsys, None, "/v1/chat/status")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_configured",
        "model": MODEL,
        "detail": "OpenAI API key is not set",
    }
    assert response.headers["X-Request-ID"]
    [started] = [line for line in lines if line["message"] == "app started"]
    assert started["openai_configured"] is False


@pytest.mark.parametrize(
    ("handler", "code", "detail", "level", "message"),
    [
        (
            key_rejected,
            503,
            "OpenAI rejected the API key or model",
            "ERROR",
            "openai check rejected",
        ),
        (
            rate_limited,
            503,
            "OpenAI is unavailable right now",
            "WARNING",
            "openai check failed",
        ),
        (
            timed_out,
            504,
            "OpenAI did not answer in time",
            "WARNING",
            "openai check timed out",
        ),
        (
            unreachable,
            503,
            "OpenAI could not be reached",
            "WARNING",
            "openai check unreachable",
        ),
    ],
    ids=["rejected-key", "rate-limited", "timeout", "unreachable"],
)
def test_openai_failures_map_to_503_or_504_and_are_logged_once(
    capsys: pytest.CaptureFixture[str],
    handler: Handler,
    code: int,
    detail: str,
    level: str,
    message: str,
) -> None:
    response, lines, _ = call(capsys, handler, "/v1/chat/status")

    assert response.status_code == code
    assert response.json() == {
        "status": "unavailable",
        "model": MODEL,
        "detail": detail,
    }
    assert response.headers["X-Request-ID"]
    [logged] = [line for line in lines if line["message"].startswith("openai check")]
    assert (logged["levelname"], logged["message"]) == (level, message)
    assert logged["model"] == MODEL
    assert logged["correlation_id"] == response.headers["X-Request-ID"]


def test_a_rejected_key_logs_openai_status_and_request_id_but_never_the_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response, lines, out = call(capsys, key_rejected, "/v1/chat/status")

    [logged] = [line for line in lines if line["message"] == "openai check rejected"]
    assert logged["error"] == "AuthenticationError"
    assert logged["openai_status"] == 401
    assert logged["openai_request_id"] == "req_test"
    for text in (out, response.text):
        assert FAKE_KEY not in text
        assert "1234" not in text  # not even the part OpenAI's message quotes


def test_the_openai_call_is_a_logfire_span_inside_the_request_trace(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, _, out = call(
        capsys, model_found, "/v1/chat/status", Settings(logfire_console=True)
    )

    text = [line for line in out.splitlines() if not line.startswith("{")]
    request = next(i for i, x in enumerate(text) if x.endswith(" GET /v1/chat/status"))
    # Indented under the request span = the OpenAI call is part of that trace.
    assert f"   OpenAI API call to '/models/{MODEL}'" in text[request + 1]


def test_an_openai_call_logs_json_only_and_nothing_to_stderr(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # capfd, not capsys: Logfire's default fallback handler holds the stderr that
    # existed at import, which capsys can't see; capfd captures the real streams.
    app = create_app(Settings())
    with TestClient(app) as client:
        app.state.openai = fake_openai(model_found)
        client.get("/v1/chat/status")
    captured = capfd.readouterr()

    lines = [json.loads(line) for line in captured.out.splitlines()]  # all JSON
    # The SDK's HTTP line is logged inside the OpenAI span, where Logfire's handler
    # would otherwise print a plain-text copy to stderr (see log_config.py).
    assert any(line["message"].startswith("HTTP Request:") for line in lines)
    assert captured.err == ""


def test_health_makes_no_openai_call(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return model_found(request)

    response, _, _ = call(capsys, handler, "/health")

    assert response.status_code == 200
    assert seen == []


def test_tests_never_read_an_openai_key_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-would-be-billed\n")
    monkeypatch.chdir(tmp_path)
    assert Settings().openai_api_key is None
