"""The OpenAI client: built once from ``Settings`` and shared by every request.

One client for the whole app, because it keeps a pool of HTTPS connections to
OpenAI that later requests reuse; ``lifespan`` creates it and closes it at shutdown.
"""

from openai import AsyncOpenAI

from .config import Settings


def build_openai_client(settings: Settings) -> AsyncOpenAI | None:
    """Create the OpenAI client from the app's settings.

    The key, timeout and retries are passed explicitly, so the SDK's own
    ``OPENAI_API_KEY`` lookup can't pick up a key behind ``Settings``' back.

    Args:
        settings: The app's settings.

    Returns:
        A client ready to use, or ``None`` when no key is configured: the app still
        runs, and the chat endpoints answer that chat is not configured. (Given no
        key, the SDK would read the environment itself and raise if it found none.)
    """
    if settings.openai_api_key is None:
        return None
    return AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_s,
        max_retries=settings.openai_max_retries,
    )
