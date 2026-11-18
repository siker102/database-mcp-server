"""``db-init``: run migrations, set roles and rights, load the data.

Runs as the owner role, never as the server's roles: the running server has no right to change
schema or data [BSI BER.4.1]. Creating the roles is optional (``--create-roles``) because in real
environments a database team usually provides them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from database_mcp.db.schema import PUBLIC_VIEW
from database_mcp.db.seed import read_rows, seed

log = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

TEMP_FILE_LIMIT = "64MB"
"""Upper bound for temporary files of ``titanic_sql`` (large sorts/joins) [BSI KONF.9.1]."""


@dataclass(frozen=True)
class RoleSpec:
    """A login role the server uses, with the password to set when creating it."""

    name: str
    password: SecretStr | None


def _alembic_upgrade(sync_conn: Connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = sync_conn
    command.upgrade(config, "head")


def _quote_ident(conn: AsyncConnection, name: str) -> str:
    return conn.dialect.identifier_preparer.quote(name)


async def _create_or_update_role(conn: AsyncConnection, role: RoleSpec) -> None:
    """Create the login role, or reset its password if it already exists.

    Role names come from configuration, not from users; they are still quoted as identifiers.
    Passwords cannot be bound as parameters in ``CREATE ROLE``, so they are passed through
    ``format('%L')`` on the server side, which quotes them as literals.
    """
    if role.password is None:
        raise ValueError(f"Password for role {role.name} is required with --create-roles")
    exists = (
        await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": role.name})
    ).first()
    verb = "ALTER" if exists else "CREATE"
    statement = (
        await conn.execute(
            text(
                "SELECT format('%s ROLE %I LOGIN PASSWORD %L', "
                "CAST(:verb AS text), CAST(:name AS text), CAST(:pw AS text))"
            ),
            {"verb": verb, "name": role.name, "pw": role.password.get_secret_value()},
        )
    ).scalar_one()
    await conn.execute(text(statement))


async def _apply_rights(conn: AsyncConnection, database: str, app: str, sql: str) -> None:
    """Grant exactly the rights the roles need, nothing more [BSI BER.4.1, DEV.2.3]."""
    q_db = _quote_ident(conn, database)
    q_app = _quote_ident(conn, app)
    q_sql = _quote_ident(conn, sql)
    statements = [
        # Nobody but the owner may create objects in the schema.
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        f"GRANT CONNECT ON DATABASE {q_db} TO {q_app}, {q_sql}",
        f"GRANT USAGE ON SCHEMA public TO {q_app}, {q_sql}",
        f"REVOKE ALL ON passengers, {PUBLIC_VIEW} FROM PUBLIC",
        f"REVOKE ALL ON passengers FROM {q_sql}",
        f"GRANT SELECT ON passengers, {PUBLIC_VIEW} TO {q_app}",
        f"GRANT SELECT ON {PUBLIC_VIEW} TO {q_sql}",
    ]
    for statement in statements:
        await conn.execute(text(statement))


async def _apply_role_defaults(conn: AsyncConnection, app: str, sql: str) -> None:
    """Set role defaults; only with ``--create-roles``, i.e. when this job manages the roles.

    Defaults are an extra brake only; the hard boundary is the GRANT. ``temp_file_limit`` may only
    be set by a superuser; without that right the job warns and continues, and the DB team sets
    it (see RUNBOOK).
    """
    q_app = _quote_ident(conn, app)
    q_sql = _quote_ident(conn, sql)
    for statement in (
        f"ALTER ROLE {q_sql} SET default_transaction_read_only = on",
        f"ALTER ROLE {q_sql} SET statement_timeout = '5s'",
        f"ALTER ROLE {q_app} SET default_transaction_read_only = on",
    ):
        await conn.execute(text(statement))
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(f"ALTER ROLE {q_sql} SET temp_file_limit = '{TEMP_FILE_LIMIT}'")
            )
    except DBAPIError:
        log.warning("temp_file_limit_not_set", role=sql, reason="needs superuser")


async def run_db_init(
    engine: AsyncEngine,
    *,
    database: str,
    data_file: Path,
    app_role: RoleSpec,
    sql_role: RoleSpec,
    create_roles: bool,
) -> int:
    """Bring the database to the target state and return the number of inserted rows.

    Every step is idempotent, so the job can run on every install and upgrade.
    """
    rows = read_rows(data_file)  # validate before touching the database
    async with engine.begin() as conn:
        await conn.run_sync(_alembic_upgrade)
    async with engine.begin() as conn:
        if create_roles:
            await _create_or_update_role(conn, app_role)
            await _create_or_update_role(conn, sql_role)
            await _apply_role_defaults(conn, app_role.name, sql_role.name)
        await _apply_rights(conn, database, app_role.name, sql_role.name)
        inserted = await seed(conn, rows)
    log.info("db_init_finished", inserted_rows=inserted, total_rows=len(rows))
    return inserted
