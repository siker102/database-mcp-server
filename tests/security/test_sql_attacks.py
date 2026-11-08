"""Attack queries against run_sql, per layer of the defense in depth [BSI TEST.3.1, DEV.2.6].

Layer 1 (validator) is tested without a database; layers 2 and 3 (execution limits, database
rights) are also tested *without* the validator, so each layer holds on its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import SQL, Database
from titanic_mcp.services.sql_guard import MAX_QUERY_LENGTH, SqlRejectedError, validate
from titanic_mcp.services.sql_runner import SqlExecutionError, SqlRunner

ATTACKS = [
    # data or schema changes
    "DELETE FROM passengers_public",
    "UPDATE passengers_public SET age = 1",
    "INSERT INTO passengers_public (passenger_id) VALUES (1000)",
    "DROP TABLE passengers",
    "TRUNCATE passengers",
    "CREATE TABLE x AS SELECT * FROM passengers_public",
    "ALTER TABLE passengers_public RENAME TO x",
    "COPY passengers TO '/tmp/out.csv'",
    "COPY (SELECT 1) TO PROGRAM 'id'",
    # session and rights
    "SET statement_timeout = 0",
    "RESET ALL",
    "GRANT SELECT ON passengers TO PUBLIC",
    "BEGIN",
    # several statements, also hidden behind comments
    "SELECT 1; DROP TABLE passengers",
    "SELECT 1 /* harmless */ ; DELETE FROM passengers_public",
    "SELECT 1; -- comment\nDELETE FROM passengers_public",
    # data-modifying CTE, SELECT INTO, row locks
    "WITH d AS (DELETE FROM passengers_public RETURNING *) SELECT * FROM d",
    "SELECT * INTO copy_of FROM passengers_public",
    "SELECT * FROM passengers_public FOR UPDATE",
    "SELECT * FROM passengers_public FOR SHARE",
    # other tables, system catalogs
    "SELECT name FROM passengers",
    "SELECT * FROM public.passengers_public",
    "SELECT * FROM pg_catalog.pg_roles",
    "SELECT * FROM pg_roles",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM passengers_public WHERE passenger_id IN (SELECT 1 FROM passengers)",
    # dangerous functions
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)",
    "SELECT lo_import('/etc/passwd')",
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT set_config('statement_timeout', '0', false)",
    "SELECT current_setting('data_directory')",
    "SELECT version()",
    "SELECT * FROM generate_series(1, 100000000)",
    "SELECT pg_terminate_backend(1)",
    # empty, garbage, too long
    "",
    "   ",
    "this is not sql",
    "SELECT " + "1," * MAX_QUERY_LENGTH + "1",
]

VALID = [
    "SELECT count(*) FROM passengers_public",
    "SELECT pclass, avg(survived) AS rate FROM passengers_public GROUP BY pclass ORDER BY pclass",
    "WITH kids AS (SELECT * FROM passengers_public WHERE age < 18) SELECT count(*) FROM kids",
    "SELECT sex, round(avg(fare)::numeric, 2) FROM passengers_public GROUP BY sex",
    "SELECT CASE WHEN age < 18 THEN 'child' ELSE 'adult' END AS g, count(*) "
    "FROM passengers_public GROUP BY 1",
    "SELECT passenger_id, rank() OVER (ORDER BY fare DESC) FROM passengers_public LIMIT 5",
    "SELECT 1 UNION SELECT 2",
    "SELECT 'a:b' AS colon_in_literal",
]


@pytest.mark.parametrize("query", ATTACKS)
def test_validator_rejects_attack(query: str) -> None:
    with pytest.raises(SqlRejectedError):
        validate(query)


@pytest.mark.parametrize("query", VALID)
def test_validator_accepts_analysis_queries(query: str) -> None:
    assert validate(query)


# ---- layers 2 and 3 against a real database ------------------------------------------------


@pytest.fixture
async def runner(database: Database) -> AsyncIterator[SqlRunner]:
    engine = database.engine(SQL, null_pool=True)
    yield SqlRunner(engine, timeout_seconds=1.0, max_concurrency=2)
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("query", VALID)
async def test_valid_queries_run(runner: SqlRunner, query: str) -> None:
    result = await runner.run(query, limit=50)
    assert result.row_count >= 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_result_is_capped_and_marked_truncated(runner: SqlRunner) -> None:
    result = await runner.run("SELECT passenger_id FROM passengers_public", limit=10)
    assert result.row_count == 10
    assert result.truncated is True
    assert result.columns == ["passenger_id"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_expensive_cross_join_is_cancelled(runner: SqlRunner) -> None:
    """~700 million rows: the statement timeout (or temp_file_limit) stops it."""
    with pytest.raises(SqlExecutionError, match=r"time limit|resources"):
        await runner.run(
            "SELECT count(*) FROM passengers_public a, passengers_public b, passengers_public c"
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_unknown_column_gives_helpful_message(runner: SqlRunner) -> None:
    with pytest.raises(SqlExecutionError, match="does not exist"):
        await runner.run("SELECT shoe_size FROM passengers_public")


@pytest.mark.integration
@pytest.mark.anyio
async def test_limit_bounds(runner: SqlRunner) -> None:
    with pytest.raises(SqlRejectedError):
        await runner.run("SELECT 1", limit=501)


# Layer 3 alone: the role titanic_sql, used directly without validator.
ROLE_ATTACKS = [
    "SELECT name FROM passengers",
    "DELETE FROM passengers_public",
    "UPDATE passengers SET name = 'x'",
    "CREATE TABLE evil (id int)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT lo_import('/etc/passwd')",
    "COPY passengers_public TO '/tmp/x'",
]


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("statement", ROLE_ATTACKS)
async def test_role_blocks_attack_without_validator(database: Database, statement: str) -> None:
    engine = database.engine(SQL, null_pool=True)
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(text(statement))
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_role_runs_read_only_by_default(database: Database) -> None:
    engine = database.engine(SQL, null_pool=True)
    async with engine.connect() as conn:
        value = (await conn.execute(text("SHOW default_transaction_read_only"))).scalar_one()
    await engine.dispose()
    assert value == "on"


@pytest.mark.integration
@pytest.mark.anyio
async def test_null_pool_prevents_session_state_leaking(database: Database) -> None:
    """Even if a query could disable the timeout for its session, the next query is unaffected.

    Simulates a validator bypass: set_config runs directly on the role's NullPool engine.
    """
    engine = database.engine(SQL, null_pool=True)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT set_config('statement_timeout', '0', false)"))
        assert (await conn.execute(text("SHOW statement_timeout"))).scalar_one() == "0"
    async with engine.connect() as conn:
        assert (await conn.execute(text("SHOW statement_timeout"))).scalar_one() == "5s"
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_sleep_is_cancelled_by_timeout_even_without_validator(database: Database) -> None:
    """Layer 2 alone: the runner's own execution path cancels a long query."""
    engine = database.engine(SQL, null_pool=True)
    runner = SqlRunner(engine, timeout_seconds=1.0, max_concurrency=1)
    with pytest.raises(Exception, match="statement timeout"):
        await runner._execute("SELECT pg_sleep(5)", limit=1, timeout_ms=500)
    await engine.dispose()
