"""Creation of SQLAlchemy async engines, one per database role.

Every role gets its own engine so that a connection can never carry more rights than the code
path needs [BSI BER.4.1]. TLS settings come from :class:`DatabaseSettings`; the default is
``verify-full`` (encryption *and* certificate check) [BSI KONF.14.1, DEV.4.9].
"""

from __future__ import annotations

import ssl
from typing import Any

from pydantic import SecretStr
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from database_mcp.config import DatabaseSettings


def build_url(db: DatabaseSettings, user: str, password: SecretStr) -> URL:
    """Build an asyncpg URL; the password stays out of string formatting and logs."""
    return URL.create(
        "postgresql+asyncpg",
        username=user,
        password=password.get_secret_value(),
        host=db.host,
        port=db.port,
        database=db.name,
    )


def ssl_argument(db: DatabaseSettings) -> ssl.SSLContext | bool:
    """Translate the configured sslmode into asyncpg's ``ssl`` connect argument.

    asyncpg does not fall back to plaintext when an SSLContext is given, so ``verify-full``
    fails loudly against a server without TLS instead of downgrading silently.
    """
    if db.sslmode == "disable":
        return False
    context = ssl.create_default_context(cafile=str(db.sslrootcert) if db.sslrootcert else None)
    if db.sslmode == "require":
        # Encryption only, no certificate check (libpq semantics of "require").
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif db.sslmode == "verify-ca":
        context.check_hostname = False
    return context


def create_engine(
    db: DatabaseSettings,
    user: str,
    password: SecretStr,
    *,
    null_pool: bool = False,
    pool_size: int = 5,
) -> AsyncEngine:
    """Create an async engine for one database role.

    Args:
        db: Shared connection settings.
        user: Database role to log in as.
        password: Password of that role.
        null_pool: Open a fresh connection per use. Used for ``run_sql`` so that session state
            changed by one query can never leak into the next one.
        pool_size: Pool size for pooled engines.
    """
    kwargs: dict[str, Any] = {"connect_args": {"ssl": ssl_argument(db)}}
    if null_pool:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = pool_size
        kwargs["pool_pre_ping"] = True
    return create_async_engine(build_url(db, user, password), **kwargs)
