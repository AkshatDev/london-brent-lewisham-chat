"""Logfire set-up: a trace for every request, with the app's own logs inside it.

Traces are sent to the Logfire service only when ``logfire_token`` is set; without
it nothing leaves the machine and spans can only be printed to the terminal.
"""

from typing import Any

import logfire

from .config import Settings

SERVICE_NAME = "london-brent-lewisham-chat"
"""Name every trace carries; fixed in code because it never changes."""

NOT_SECRET_KEYS = frozenset({"session_id", "local_authority"})
"""Keys Logfire's default scrubbing would hide by name alone, though they hold no
secret: ``session_id`` matches its "session" pattern (meant for session cookies),
``local_authority`` its "auth" pattern (meant for authorization headers). Only
these exact keys are let through; everything else is still scrubbed."""


def configure_logfire(settings: Settings) -> None:
    """Configure the Logfire SDK for the whole process.

    Every option that matters is passed explicitly, so ``LOGFIRE_*`` environment
    variables or a local ``.logfire/`` credentials folder can't change what is sent
    or printed behind ``Settings``' back: sending depends on the token alone.
    Scrubbing stays on, with an explicit allow-list (``NOT_SECRET_KEYS``).

    Args:
        settings: The app's settings; ``logfire_token`` turns sending on,
            ``logfire_console`` turns terminal output on, and ``environment`` tags
            every trace (filter on ``deployment.environment.name`` in Logfire).
    """
    token = settings.logfire_token
    logfire.configure(
        send_to_logfire=token is not None,
        token=token.get_secret_value() if token is not None else None,
        service_name=SERVICE_NAME,
        environment=settings.environment,
        console=logfire.ConsoleOptions() if settings.logfire_console else False,
        scrubbing=logfire.ScrubbingOptions(callback=_keep_not_secret_keys),
    )


def _keep_not_secret_keys(match: logfire.ScrubMatch) -> Any:  # noqa: ANN401
    """Let a value through scrubbing only if its key is on the allow-list.

    Logfire calls this for each value it is about to redact. The key is the last
    part of the value's path, e.g. ``('attributes', 'session_id')`` or, inside the
    tool arguments, ``('attributes', 'args', 'local_authority')``.

    Args:
        match: The value Logfire is about to redact, with its path.

    Returns:
        The value, kept as it is, for an allow-listed key; otherwise ``None``,
        which lets Logfire redact it.
    """
    if match.path and match.path[-1] in NOT_SECRET_KEYS:
        return match.value
    return None
