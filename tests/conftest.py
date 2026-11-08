"""Shared fixtures: one PostgreSQL container per test session, initialised with db-init."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from database_mcp.config import DatabaseSettings
from database_mcp.db.engine import create_engine
from database_mcp.db.init_db import RoleSpec, run_db_init

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "titanic.csv"
POSTGRES_IMAGE = os.environ.get("DBMCP_TEST_POSTGRES_IMAGE", "postgres:17-alpine")

OWNER = ("titanic_owner", SecretStr("owner-test-pw"))
APP = ("titanic_app", SecretStr("app-test-pw"))
SQL = ("titanic_sql", SecretStr("sql-test-pw"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True)
class Database:
    """Connection data of the test database."""

    settings: DatabaseSettings

    def engine(self, role: tuple[str, SecretStr], *, null_pool: bool = False) -> AsyncEngine:
        return create_engine(self.settings, role[0], role[1], null_pool=null_pool)


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    """Start PostgreSQL in Docker and run db-init (with role creation) once."""
    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer(
        POSTGRES_IMAGE,
        username=OWNER[0],
        password=OWNER[1].get_secret_value(),
        dbname="titanic",
        driver=None,
    )
    with container:
        settings = DatabaseSettings(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            name="titanic",
            sslmode="disable",
        )
        db = Database(settings)

        async def init() -> None:
            engine = db.engine(OWNER)
            try:
                await run_db_init(
                    engine,
                    database="titanic",
                    data_file=DATA_FILE,
                    app_role=RoleSpec(*APP),
                    sql_role=RoleSpec(*SQL),
                    create_roles=True,
                )
            finally:
                await engine.dispose()

        asyncio.run(init())
        yield db


@pytest.fixture
async def app_engine(database: Database) -> AsyncIterator[AsyncEngine]:
    engine = database.engine(APP)
    yield engine
    await engine.dispose()
