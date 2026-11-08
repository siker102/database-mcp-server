"""Execution of validated free SQL (layer 2 of the defense in depth).

- Own engine for the role ``titanic_sql`` with ``NullPool``: every query gets a fresh connection,
  so session settings a query might change never reach the next query.
- Read-only transaction with ``SET LOCAL statement_timeout``, plus an application timeout as a
  fallback if the database does not answer.
- ``fetchmany(limit + 1)`` on a server-side cursor: never more than ``limit`` rows in memory,
  and the extra row tells whether the result was truncated.
- A semaphore limits parallel queries per pod, so a few expensive queries cannot saturate the
  database [BSI KONF.15.3].
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg
import structlog
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from database_mcp.services.sql_guard import SqlRejectedError, validate

log = structlog.get_logger(__name__)

MAX_ROWS = 500
DEFAULT_ROWS = 100


class SqlResult(BaseModel):
    """Result of ``run_sql``."""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    """True when the query returned more rows than the limit."""


class SqlExecutionError(Exception):
    """The query failed in the database; the message is safe for the agent."""


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.date | dt.datetime | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    return value


def _explain_db_error(exc: Exception) -> str:
    """Map database errors to messages that help the agent but reveal no internals.

    Errors arrive wrapped by SQLAlchemy (``DBAPIError.orig``) or, while streaming rows, as the
    raw asyncpg exception; both carry the SQLSTATE code.
    """
    orig = getattr(exc, "orig", None) or exc
    sqlstate = getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )
    if sqlstate == "57014":
        return "Query cancelled: it exceeded the time limit."
    if sqlstate == "42501":
        return "Permission denied: only the view passengers_public can be read."
    if sqlstate == "53100" or sqlstate == "54000":
        return "Query needs too many resources (e.g. temporary disk space)."
    if sqlstate and sqlstate.startswith(("42", "22")):
        # Syntax, unknown column or bad value: the message only names parts of the agent's own
        # query, so it is safe and helps the agent to fix it.
        args = getattr(orig, "args", None) or [""]
        detail = str(args[0]).splitlines()[0][:200]
        return f"Query error: {detail}"
    return "Query failed in the database."


class SqlRunner:
    """Validate and execute free SELECT queries with the role ``titanic_sql``."""

    def __init__(self, engine: AsyncEngine, *, timeout_seconds: float, max_concurrency: int):
        self._engine = engine
        self._timeout = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run(self, query: str, *, limit: int = DEFAULT_ROWS) -> SqlResult:
        """Run ``query`` and return at most ``limit`` rows.

        Raises:
            SqlRejectedError: The validator rejected the query.
            SqlExecutionError: The database rejected or cancelled it.
        """
        if not 1 <= limit <= MAX_ROWS:
            raise SqlRejectedError(f"limit must be between 1 and {MAX_ROWS}.")
        statement = validate(query)
        timeout_ms = int(self._timeout * 1000)
        async with self._semaphore:
            try:
                # Fallback timer slightly above the database timeout.
                async with asyncio.timeout(self._timeout + 2):
                    return await self._execute(statement, limit, timeout_ms)
            except TimeoutError as exc:
                raise SqlExecutionError("Query cancelled: it exceeded the time limit.") from exc
            except (DBAPIError, asyncpg.PostgresError) as exc:
                log.info("sql_failed", sqlstate=getattr(exc, "sqlstate", None))
                raise SqlExecutionError(_explain_db_error(exc)) from exc

    async def _execute(self, statement: str, limit: int, timeout_ms: int) -> SqlResult:
        async with self._engine.connect() as conn, conn.begin():
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
            # Escape colons so SQLAlchemy does not read ":name" in the agent's SQL as a bind
            # parameter; the database receives the validated text unchanged.
            result = await conn.stream(text(statement.replace(":", r"\:")))
            columns = list(result.keys())
            batch = await result.fetchmany(limit + 1)
            await result.close()
        rows = [[_json_value(v) for v in row] for row in batch[:limit]]
        return SqlResult(
            columns=columns, rows=rows, row_count=len(rows), truncated=len(batch) > limit
        )
