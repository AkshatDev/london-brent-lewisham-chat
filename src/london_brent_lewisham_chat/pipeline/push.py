"""Copy the bot's finished table(s) from the local PostGIS to a hosted database (Neon).

Today that is ``core.applications`` only (``schema.PUSHED_TABLES``).

All cleaning happens locally; the hosted database only receives the result. One
borough at a time, in one transaction on the hosted side, so the bot there sees
either the old rows or the new ones, never half. ``raw`` is never pushed (PLAN.md
§11: raw snapshots stay local).
"""

import logging
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from ..config import PipelineSettings
from ..scope import Borough, borough_name
from . import schema

logger = logging.getLogger(__name__)

COLUMNS_SQL = """
select a.attname, format_type(a.atttypid, a.atttypmod)
from pg_attribute a
where a.attrelid = to_regclass(%s) and a.attnum > 0 and not a.attisdropped
order by a.attnum
"""


def columns(conn: psycopg.Connection, table: str) -> list[tuple[str, str]]:
    """List a table's columns and types in order (empty if the table doesn't exist).

    Args:
        conn: Open connection.
        table: Schema-qualified table name, e.g. ``core.applications``.

    Returns:
        ``(name, type)`` pairs, e.g. ``("centroid", "geometry(Point,4326)")``.
    """
    return [tuple(row) for row in conn.execute(COLUMNS_SQL, [table]).fetchall()]


def prepare_table(
    target: psycopg.Connection, table: str, wanted: list[tuple[str, str]]
) -> bool:
    """Make the hosted table match the local one, rebuilding it if it doesn't.

    A mismatch means the local schema changed since the last push; the hosted table is
    only a copy, so it is dropped and created again from the shared definition.

    Args:
        target: Connection to the hosted database (inside the push transaction).
        table: Schema-qualified table name.
        wanted: The local table's columns and types.

    Returns:
        ``True`` if the table was (re)created.

    Raises:
        RuntimeError: If the created table still differs from the local one.
    """
    existing = columns(target, table)
    if existing == wanted:
        return False
    if existing:
        logger.warning(
            "hosted table differs from local, recreating", extra={"table": table}
        )
        target.execute(
            sql.SQL("drop table {}").format(sql.Identifier(*table.split(".")))
        )
    target.execute(schema.PUSHED_TABLES[table])
    if columns(target, table) != wanted:
        raise RuntimeError(
            f"{table}: hosted definition differs from local after create"
        )
    return True


def copy_rows(
    source: psycopg.Connection, target: psycopg.Connection, table: str, name: str
) -> None:
    """Stream one borough's rows from the local table into the hosted one.

    Rows are replaced, not merged: the borough's old rows are deleted first. COPY
    text format carries geometry as hex EWKB, which any PostGIS version reads.

    Args:
        source: Connection to the local database.
        target: Connection to the hosted database (inside the push transaction).
        table: Schema-qualified table name.
        name: The borough as stored, e.g. ``"Brent"``.
    """
    ident = sql.Identifier(*table.split("."))
    target.execute(sql.SQL("delete from {} where borough = %s").format(ident), [name])
    out = sql.SQL("copy (select * from {} where borough = {}) to stdout").format(
        ident, sql.Literal(name)
    )
    with (
        source.cursor().copy(out) as reader,
        target.cursor().copy(sql.SQL("copy {} from stdin").format(ident)) as writer,
    ):
        for block in reader:
            writer.write(block)


def count(conn: psycopg.Connection, table: str, name: str) -> int:
    """Count one borough's rows in a table.

    Args:
        conn: Open connection.
        table: Schema-qualified table name.
        name: The borough as stored.

    Returns:
        The number of rows.
    """
    query = sql.SQL("select count(*) from {} where borough = %s").format(
        sql.Identifier(*table.split("."))
    )
    return conn.execute(query, [name]).fetchone()[0]


def run(settings: PipelineSettings, borough: Borough) -> dict[str, Any]:
    """Push one borough's rows of every bot table to the hosted database.

    Args:
        settings: Pipeline settings: the local database and ``neon_database_url``.
        borough: One of the allowed boroughs.

    Returns:
        Rows pushed per table, and the tables that were (re)created.

    Raises:
        ValueError: If ``NEON_DATABASE_URL`` is not set.
        RuntimeError: If a table is empty locally, or a count differs after the copy
            (the hosted transaction is then rolled back).
    """
    url = (
        settings.neon_database_url.get_secret_value()
        if settings.neon_database_url
        else ""
    )
    if not url.strip():  # `NEON_DATABASE_URL=` with no value counts as not set
        raise ValueError("set NEON_DATABASE_URL in .env to push")
    name = borough_name(borough)
    host = conninfo_to_dict(url).get("host")
    pushed: dict[str, int] = {}
    created: list[str] = []
    with (
        psycopg.connect(settings.conninfo()) as source,
        psycopg.connect(url) as target,  # one transaction: commits on success only
    ):
        target.execute(schema.EXTENSIONS_SQL)
        for schema_name in sorted({t.split(".")[0] for t in schema.PUSHED_TABLES}):
            target.execute(
                sql.SQL("create schema if not exists {}").format(
                    sql.Identifier(schema_name)
                )
            )
        for table in schema.PUSHED_TABLES:
            local = count(source, table, name)
            if local == 0:
                raise RuntimeError(
                    f"{table} has no {name} rows locally; run load first"
                )
            if prepare_table(target, table, columns(source, table)):
                created.append(table)
            copy_rows(source, target, table, name)
            hosted = count(target, table, name)
            if hosted != local:
                raise RuntimeError(f"{table}: pushed {hosted} rows, local has {local}")
            pushed[table] = hosted
    with psycopg.connect(url, autocommit=True) as conn:  # fresh planner statistics
        for table in schema.PUSHED_TABLES:
            conn.execute(
                sql.SQL("analyze {}").format(sql.Identifier(*table.split(".")))
            )
    logger.info(
        "push finished",
        extra={"borough": borough, "host": host, "rows": pushed, "recreated": created},
    )
    return {"rows": pushed, "recreated": created}
