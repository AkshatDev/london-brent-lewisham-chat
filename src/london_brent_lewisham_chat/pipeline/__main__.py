"""Command line for the pipeline: ``uv run python -m london_brent_lewisham_chat.pipeline``.

Examples:
    extract brent           download today's raw snapshot
    postcodes brent         look up postcodes for placeholder centroids (postcodes.io)
    load brent              load the newest snapshot into PostGIS
    push brent              copy the bot's tables to NEON_DATABASE_URL (from .env)
"""

import argparse
import logging
from datetime import date
from typing import get_args

import httpx

from ..config import PipelineSettings
from ..log_config import configure_logging
from ..scope import Borough
from . import extract

logger = logging.getLogger(__name__)


def _client(settings: PipelineSettings) -> httpx.Client:
    """Build the Datahub HTTP client; the header value is never logged.

    Args:
        settings: Pipeline settings with the URL and the header value.

    Returns:
        A client with base URL, header and a generous timeout.
    """
    return httpx.Client(
        base_url=settings.datahub_url,
        headers={
            "X-API-AllowRequest": settings.datahub_allow_request.get_secret_value()
        },
        timeout=120,
    )


def main() -> None:
    """Parse the command line and run one pipeline step."""
    parser = argparse.ArgumentParser(prog="pipeline")
    parser.add_argument("step", choices=["extract", "postcodes", "load", "push"])
    # choices come from the allow-list in code: no other borough can be named.
    parser.add_argument("borough", choices=get_args(Borough))
    args = parser.parse_args()

    settings = PipelineSettings()
    configure_logging(settings.log_level)
    borough: Borough = args.borough

    if args.step == "extract":
        with _client(settings) as client:
            extract.write_snapshot(client, borough, settings.data_dir, date.today())
    elif args.step == "postcodes":
        from . import load, postcodes

        path, _ = load.newest_snapshot(settings.data_dir, borough)
        postcodes.refresh_cache(
            extract.read_snapshot(path, borough), settings.data_dir, borough
        )
    elif args.step == "push":
        from . import push

        push.run(settings, borough)
    else:
        from . import load  # imported here so `extract` works without a database

        load.run(settings, borough)


if __name__ == "__main__":
    main()
