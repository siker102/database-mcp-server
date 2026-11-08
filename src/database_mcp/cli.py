"""Command line interface: ``database-mcp serve`` and ``database-mcp db-init``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import structlog
from pydantic import ValidationError

from database_mcp.config import DatabaseSettings, InitSettings, ServerSettings
from database_mcp.db.engine import create_engine
from database_mcp.db.init_db import RoleSpec, run_db_init
from database_mcp.db.seed import SeedError
from database_mcp.observability import configure_logging

log = structlog.get_logger(__name__)


def _db_init(create_roles: bool) -> int:
    settings = InitSettings()
    db = DatabaseSettings()
    configure_logging(settings.log_level)
    engine = create_engine(db, settings.db_owner_user, settings.db_owner_password, pool_size=1)

    async def run() -> int:
        try:
            return await run_db_init(
                engine,
                database=db.name,
                data_file=settings.data_file,
                app_role=RoleSpec(settings.db_app_user, settings.db_app_password),
                sql_role=RoleSpec(settings.db_sql_user, settings.db_sql_password),
                create_roles=create_roles,
            )
        finally:
            await engine.dispose()

    asyncio.run(run())
    return 0


def _serve() -> int:
    # Imported lazily so `db-init` does not need the server settings (API keys file etc.).
    from database_mcp.app import serve

    settings = ServerSettings()
    db = DatabaseSettings()
    configure_logging(settings.log_level)
    serve(settings, db)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the ``database-mcp`` command. Returns the process exit code."""
    parser = argparse.ArgumentParser(prog="database-mcp", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Run the MCP server (HTTP).")
    init = sub.add_parser("db-init", help="Run migrations, set rights and load the data.")
    init.add_argument(
        "--create-roles",
        action="store_true",
        help="Also create the login roles of the server (needs CREATEROLE).",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "db-init":
            return _db_init(args.create_roles)
        return _serve()
    except ValidationError as exc:
        # Invalid configuration: stop at once with the field names, never the values.
        fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()})
        log.error("invalid_configuration", fields=fields)
        return 2
    except SeedError as exc:
        log.error("invalid_data_file", error=str(exc))
        return 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
