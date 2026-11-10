"""Logfire set-up: a trace for every request, with the app's own logs inside it.

Traces are sent to the Logfire service only when ``logfire_token`` is set; without
it nothing leaves the machine and spans can only be printed to the terminal.
"""

import logfire

from .config import Settings

SERVICE_NAME = "london-brent-lewisham-chat"
"""Name every trace carries; fixed in code because it never changes."""


def configure_logfire(settings: Settings) -> None:
    """Configure the Logfire SDK for the whole process.

    Every option that matters is passed explicitly, so ``LOGFIRE_*`` environment
    variables or a local ``.logfire/`` credentials folder can't change what is sent
    or printed behind ``Settings``' back: sending depends on the token alone.

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
    )
