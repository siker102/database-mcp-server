"""db-init against a real PostgreSQL: data, idempotency, rights and TLS behaviour."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from database_mcp.config import DatabaseSettings
from database_mcp.db.engine import create_engine
from database_mcp.db.init_db import RoleSpec, run_db_init
from tests.conftest import APP, DATA_FILE, OWNER, SQL, Database

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_all_rows_loaded(database: Database) -> None:
    engine = database.engine(APP)
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM passengers"))).scalar_one()
    await engine.dispose()
    assert count == 891


async def test_second_run_changes_nothing(database: Database) -> None:
    engine = database.engine(OWNER)
    inserted = await run_db_init(
        engine,
        database="titanic",
        data_file=DATA_FILE,
        app_role=RoleSpec(*APP),
        sql_role=RoleSpec(*SQL),
        create_roles=True,
    )
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM passengers"))).scalar_one()
    await engine.dispose()
    assert inserted == 0
    assert count == 891


async def test_sql_role_reads_only_the_public_view(database: Database) -> None:
    engine = database.engine(SQL)
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM passengers_public"))).scalar_one()
    assert count == 891
    with pytest.raises(DBAPIError, match="permission denied"):
        async with engine.connect() as conn:
            await conn.execute(text("SELECT name FROM passengers LIMIT 1"))
    await engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM passengers_public",
        "UPDATE passengers SET name = 'x'",
        "INSERT INTO passengers_public (passenger_id) VALUES (9999)",
        "CREATE TABLE evil (id int)",
        "DROP VIEW passengers_public",
    ],
)
async def test_sql_role_cannot_write(database: Database, statement: str) -> None:
    """The database itself blocks writes, independent of any validator."""
    engine = database.engine(SQL)
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(text(statement))
    await engine.dispose()


async def test_app_role_cannot_write(database: Database) -> None:
    engine = database.engine(APP)
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE passengers SET name = 'x'"))
    await engine.dispose()


async def test_verify_full_does_not_downgrade(database: Database) -> None:
    """The test database has no TLS: verify-full must refuse instead of falling back."""
    settings = DatabaseSettings(
        host=database.settings.host,
        port=database.settings.port,
        name="titanic",
        sslmode="verify-full",
    )
    engine = create_engine(settings, APP[0], APP[1])
    with pytest.raises((DBAPIError, ConnectionError, OSError)):
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    await engine.dispose()


async def test_missing_password_with_create_roles(database: Database) -> None:
    engine = database.engine(OWNER)
    with pytest.raises(ValueError, match="Password for role"):
        await run_db_init(
            engine,
            database="titanic",
            data_file=DATA_FILE,
            app_role=RoleSpec(APP[0], None),
            sql_role=RoleSpec(*SQL),
            create_roles=True,
        )
    await engine.dispose()
