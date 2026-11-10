"""Pushing to the hosted database needs its URL and only ever sends the bot's tables."""

import pytest
from pydantic import SecretStr

from london_brent_lewisham_chat.config import PipelineSettings
from london_brent_lewisham_chat.pipeline import push, schema


def settings(url: str | None) -> PipelineSettings:
    """Pipeline settings with a placeholder local database and the given push URL."""
    return PipelineSettings(
        _env_file=None,
        datahub_allow_request="x",
        postgres_user="u",
        postgres_password="p",
        postgres_db="d",
        neon_database_url=None if url is None else SecretStr(url),
    )


@pytest.mark.parametrize("url", [None, "", "   "])
def test_push_refuses_to_run_without_a_hosted_database_url(url: str | None) -> None:
    with pytest.raises(ValueError, match="NEON_DATABASE_URL"):
        push.run(settings(url), "brent")


def test_push_refuses_a_borough_outside_the_allow_list() -> None:
    with pytest.raises(ValueError, match="out of scope"):
        push.run(settings("postgresql://h/db"), "elsewhere")  # type: ignore[arg-type]


def test_only_the_bot_table_is_pushed_never_raw() -> None:
    assert list(schema.PUSHED_TABLES) == ["core.applications"]


def test_every_pushed_table_keeps_the_borough_check_constraint() -> None:
    for ddl in schema.PUSHED_TABLES.values():
        assert "check (borough in ('Brent', 'Lewisham'))" in ddl
