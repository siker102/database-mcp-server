"""Alembic environment.

Migrations only run through ``database-mcp db-init``, which hands over an open connection of the
owner role via ``config.attributes["connection"]``. There is no URL in a config file, so no
credentials end up in the repository.
"""

from alembic import context

from database_mcp.db.schema import metadata

connection = context.config.attributes.get("connection")
if connection is None:  # pragma: no cover - guard against running plain `alembic upgrade`
    raise RuntimeError("Run migrations via `database-mcp db-init`, not the alembic CLI.")

context.configure(connection=connection, target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
