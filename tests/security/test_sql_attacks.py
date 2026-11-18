"""Attack queries against run_sql, per layer of the defense in depth [BSI TEST.3.1, DEV.2.6].

Layer 1 (validator) is tested without a database; layers 2 and 3 (execution limits, database
rights) are also tested *without* the validator, so each layer holds on its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from database_mcp.services.sql_guard import MAX_QUERY_LENGTH, SqlRejectedError, validate
from database_mcp.services.sql_runner import SqlExecutionError, SqlRunner
from tests.conftest import SQL, Database

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
    # statements that sqlglot only parses as an opaque command
    "EXPLAIN ANALYZE SELECT * FROM passengers_public",
    "SHOW ALL",
    "PREPARE p AS SELECT 1",
    "EXECUTE p",
    "DO $$ BEGIN PERFORM pg_sleep(10); END $$",
    "CALL some_procedure()",
    "VACUUM",
    "LISTEN channel",
    "VALUES (1)",
    # other tables in disguise
    'SELECT * FROM "public"."passengers"',
    'SELECT * FROM "PASSENGERS_PUBLIC"',  # quoted: a different relation than passengers_public
    "TABLE passengers",
    "SELECT * FROM ONLY passengers",
    "SELECT * FROM passengers_public p, LATERAL (SELECT name FROM passengers) x",
    "SELECT (SELECT name FROM passengers LIMIT 1)",
    "SELECT * FROM passengers_public NATURAL JOIN pg_roles",
    "SELECT * FROM pass/**/engers",
    "SELECT * FROM ROWS FROM (generate_series(1, 3))",
    "SELECT * FROM unnest(ARRAY[1, 2])",
    # row locks in every variant
    "SELECT * FROM passengers_public FOR NO KEY UPDATE",
    "SELECT * FROM passengers_public FOR KEY SHARE",
    # functions that reveal the environment
    "SELECT current_user",
    "SELECT current_database()",
    "SELECT inet_server_addr()",
    "SELECT txid_current()",
    "SELECT pg_catalog.lower('A')",
    "SELECT row_to_json(p) FROM passengers_public p",
]

# Regression: a CTE name is only valid where PostgreSQL makes the CTE visible. Before the fix the
# validator collected CTE names from the whole query, so a CTE in an inner scope whitelisted a
# real relation of the same name in an outer scope. Catalog views like pg_roles are readable by
# every role, so the database grants would NOT have stopped this.
CTE_SCOPE_BYPASSES = [
    "SELECT * FROM (WITH pg_roles AS (SELECT 1) SELECT 1) AS x, pg_roles",
    "SELECT * FROM (WITH pg_user AS (SELECT 1) SELECT 1) AS x, pg_user",
    "SELECT * FROM (WITH passengers AS (SELECT 1) SELECT 1) AS x, passengers",
    "WITH a AS (WITH pg_stat_activity AS (SELECT 1) SELECT 1) SELECT * FROM a, pg_stat_activity",
    "SELECT * FROM passengers_public WHERE EXISTS (WITH pg_user AS (SELECT 1) SELECT 1) "
    "AND passenger_id IN (SELECT 1 FROM pg_user)",
    # inside its own definition a non-recursive CTE refers to the real relation
    "WITH pg_roles AS (SELECT * FROM pg_roles) SELECT * FROM pg_roles",
    # a CTE does not see siblings defined after it
    "WITH a AS (SELECT * FROM pg_roles), pg_roles AS (SELECT 1) SELECT * FROM a",
    # quoted CTE names are case-sensitive, the unquoted reference is the catalog view
    'WITH "PG_ROLES" AS (SELECT 1) SELECT * FROM pg_roles',
]

# Object identifier types turn a string into a catalog lookup ("does role X exist?").
TYPE_ATTACKS = [
    "SELECT 'pg_authid'::regclass",
    "SELECT CAST('passengers' AS regclass)",
    "SELECT 'titanic_owner'::regrole",
    "SELECT 'pg_sleep'::regproc",
    "SELECT 1::oid",
    "SELECT fare::pg_catalog.text FROM passengers_public",
    "SELECT ARRAY[1]::int[]",
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
    # CTE chains, nesting and recursion stay usable after the scope fix
    "WITH a AS (SELECT * FROM passengers_public), b AS (SELECT * FROM a) SELECT count(*) FROM b",
    "WITH a AS (SELECT * FROM passengers_public) SELECT count(*) FROM (SELECT * FROM a) s",
    "WITH a AS (WITH b AS (SELECT * FROM passengers_public) SELECT * FROM b) "
    "SELECT count(*) FROM a",
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5) "
    "SELECT count(*) FROM r",
    'WITH "Kids" AS (SELECT * FROM passengers_public WHERE age < 18) SELECT count(*) FROM "Kids"',
    'SELECT count(*) FROM "passengers_public"',
    "SELECT count(*) FROM PASSENGERS_PUBLIC",
    # ordinary casts
    "SELECT age::int, fare::double precision, sex::text, survived::numeric FROM passengers_public",
    "SELECT CAST(fare AS decimal(8, 2)) FROM passengers_public",
    # dollar quoting and long IN lists are fine
    "SELECT $$it's a string$$ AS s",
    "SELECT count(*) FROM passengers_public WHERE passenger_id IN ("
    + ", ".join(str(i) for i in range(1, 600))
    + ")",
]


@pytest.mark.parametrize("query", ATTACKS + CTE_SCOPE_BYPASSES + TYPE_ATTACKS)
def test_validator_rejects_attack(query: str) -> None:
    with pytest.raises(SqlRejectedError):
        validate(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT count(*) /* x */ FROM passengers_public -- ; DROP TABLE x",
        # a line comment containing "/*" would become a nested block comment in PostgreSQL
        "SELECT 1 AS a -- /*\n, 2 AS b",
    ],
)
def test_validated_text_has_no_comments(query: str) -> None:
    """Regression: the runner executes the regenerated SQL, so it must not carry comments.

    sqlglot re-emits comments as /* ... */ and PostgreSQL nests block comments, so a comment could
    make the executed text differ from the validated one.
    """
    statement = validate(query)
    assert "--" not in statement
    assert "/*" not in statement
    assert "DROP" not in statement.upper()


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
@pytest.mark.parametrize("limit", [0, -1, 501])
async def test_limit_bounds(runner: SqlRunner, limit: int) -> None:
    with pytest.raises(SqlRejectedError):
        await runner.run("SELECT 1", limit=limit)


@pytest.mark.integration
@pytest.mark.anyio
async def test_limit_at_the_maximum_is_accepted(runner: SqlRunner) -> None:
    result = await runner.run("SELECT passenger_id FROM passengers_public", limit=500)
    assert result.row_count == 500
    assert result.truncated is True


@pytest.mark.integration
@pytest.mark.anyio
async def test_dollar_quoted_text_stays_data(runner: SqlRunner) -> None:
    """Regenerating the SQL must not turn quoted text into code (quotes are re-escaped)."""
    result = await runner.run("SELECT $$it's; DELETE FROM passengers_public$$ AS s")
    assert result.rows == [["it's; DELETE FROM passengers_public"]]
    count = await runner.run("SELECT count(*) FROM passengers_public")
    assert count.rows == [[891]]


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("query", CTE_SCOPE_BYPASSES[:2])
async def test_runner_rejects_cte_scope_bypass(runner: SqlRunner, query: str) -> None:
    with pytest.raises(SqlRejectedError):
        await runner.run(query)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("view", ["pg_roles", "pg_user", "pg_stat_activity"])
async def test_catalog_views_are_readable_by_the_sql_role(database: Database, view: str) -> None:
    """Documents why the validator's table allowlist matters: PostgreSQL grants every role read
    access to these catalog views, so layer 3 (rights) does not protect them; layer 1 must."""
    engine = database.engine(SQL, null_pool=True)
    async with engine.connect() as conn:
        rows = (await conn.execute(text(f"SELECT count(*) FROM {view}"))).scalar_one()
    await engine.dispose()
    assert rows >= 1


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
