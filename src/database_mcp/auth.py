"""API keys, scopes and rate limiting.

Key format ``dbmcp_<key_id>_<secret>``; only a salted SHA-256 hash of the secret is stored
[BSI DEV.3.4, TR-02102-1]. One key belongs to exactly one agent of one team [BSI BER.3.16] and
always expires [BSI BER.7.10]. The check plugs into the MCP SDK's ``TokenVerifier`` interface,
so the SDK's bearer middleware answers missing or wrong keys with 401.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import structlog
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator
from starlette.types import ASGIApp, Receive, Send
from starlette.types import Scope as AsgiScope

from database_mcp.observability import AUTH_FAILURES, RATE_LIMITED

log = structlog.get_logger(__name__)

KEY_PREFIX = "dbmcp_"
SECRET_BYTES = 32
"""256 bit of randomness from the OS CSPRNG via ``secrets`` [BSI BER.7.2, BER.7.5]."""
SALT_BYTES = 16
MAX_LIFETIME = {True: timedelta(days=90), False: timedelta(days=365)}
"""Maximum key lifetime: 90 days with ``pii`` (compensation for deviation A3), else 365 days."""

KeyId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]{8,32}$")]
_KEY_PATTERN = re.compile(r"^dbmcp_([a-z0-9]{8,32})_([A-Za-z0-9_-]{43,})$")


class Scope(StrEnum):
    """Permissions a key can carry (see CLAUDE.md, "Berechtigungen")."""

    READ = "read"
    PII = "pii"
    SQL = "sql"


class ApiKeyEntry(BaseModel):
    """One entry of the key configuration file. Contains no secret, only its salted hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key_id: KeyId
    agent: str = Field(min_length=1, max_length=64)
    team: str = Field(min_length=1, max_length=64)
    scopes: frozenset[Scope] = Field(min_length=1)
    expires_at: datetime
    salt: str = Field(pattern=r"^[0-9a-f]{32}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _check_expiry(self) -> ApiKeyEntry:
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return self


_ENTRIES = TypeAdapter(list[ApiKeyEntry])


class KeyConfigError(ValueError):
    """The key configuration is invalid; the server must not start."""


def hash_secret(salt_hex: str, secret: str) -> str:
    """Return ``SHA-256(salt || secret)`` as hex."""
    return hashlib.sha256(bytes.fromhex(salt_hex) + secret.encode()).hexdigest()


def load_keys(path: Path, *, now: datetime | None = None) -> dict[str, ApiKeyEntry]:
    """Load and validate the key file; raise :class:`KeyConfigError` on any problem.

    A key whose expiry lies further in the future than its maximum lifetime is rejected, so a
    long-lived ``pii`` key cannot be configured by accident.
    """
    now = now or datetime.now(UTC)
    try:
        entries = _ENTRIES.validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise KeyConfigError(f"Cannot read API key file {path}: {type(exc).__name__}") from exc
    keys: dict[str, ApiKeyEntry] = {}
    for entry in entries:
        if entry.key_id in keys:
            raise KeyConfigError(f"Duplicate key_id {entry.key_id}")
        limit = MAX_LIFETIME[Scope.PII in entry.scopes]
        if entry.expires_at > now + limit:
            raise KeyConfigError(
                f"Key {entry.key_id} expires later than the allowed {limit.days} days"
            )
        keys[entry.key_id] = entry
    return keys


@dataclass(frozen=True)
class GeneratedKey:
    """A new key: the secret token (shown once) and its configuration entry."""

    token: str
    entry: ApiKeyEntry


def generate_key(
    *, agent: str, team: str, scopes: set[Scope], days: int, now: datetime | None = None
) -> GeneratedKey:
    """Create a new random key and the matching configuration entry."""
    now = now or datetime.now(UTC)
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    salt = secrets.token_hex(SALT_BYTES)
    entry = ApiKeyEntry(
        key_id=key_id,
        agent=agent,
        team=team,
        scopes=frozenset(scopes),
        expires_at=(now + timedelta(days=days)).replace(microsecond=0),
        salt=salt,
        sha256=hash_secret(salt, secret),
    )
    return GeneratedKey(token=f"{KEY_PREFIX}{key_id}_{secret}", entry=entry)


def entry_to_json(entry: ApiKeyEntry) -> dict[str, Any]:
    """Serialize an entry for the key file (scopes sorted for stable diffs)."""
    data = entry.model_dump(mode="json")
    data["scopes"] = sorted(data["scopes"])
    return data


class ApiKeyVerifier:
    """MCP SDK ``TokenVerifier`` that checks ``dbmcp_`` keys against the configuration.

    Every outcome is logged with ``key_id`` only, never the key [BSI DET.3.1.1]. Failures return
    ``None``; the SDK then answers 401 without saying what was wrong [BSI DEV.3.3].
    """

    def __init__(self, keys: dict[str, ApiKeyEntry]) -> None:
        self._keys = keys

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an access token for a valid key, otherwise ``None``."""
        # fullmatch, not match: "$" would also match before a trailing newline.
        match = _KEY_PATTERN.fullmatch(token)
        key_id = match.group(1) if match else None
        entry = self._keys.get(key_id) if key_id else None
        if match is None or entry is None:
            return self._fail("unknown_key", key_id)
        if not hmac.compare_digest(hash_secret(entry.salt, match.group(2)), entry.sha256):
            return self._fail("wrong_secret", key_id)
        if entry.expires_at <= datetime.now(UTC):
            return self._fail("expired", key_id)
        log.info("auth_success", key_id=entry.key_id, agent=entry.agent, team=entry.team)
        return AccessToken(
            token=f"key:{entry.key_id}",  # never keep the secret in memory longer than needed
            client_id=entry.key_id,
            scopes=sorted(entry.scopes),
            expires_at=int(entry.expires_at.timestamp()),
            claims={"agent": entry.agent, "team": entry.team},
        )

    @staticmethod
    def _fail(reason: str, key_id: str | None) -> None:
        AUTH_FAILURES.inc()
        log.warning("auth_failure", reason=reason, key_id=key_id)
        return None


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of the current request."""

    key_id: str
    agent: str
    team: str
    scopes: frozenset[str] = field(default_factory=frozenset)

    def has(self, scope: Scope) -> bool:
        """Return whether the caller holds ``scope``."""
        return scope.value in self.scopes

    @classmethod
    def from_access_token(cls, token: AccessToken) -> Principal:
        """Build the principal from the SDK access token created by the verifier."""
        claims = token.claims or {}
        return cls(
            key_id=token.client_id,
            agent=str(claims.get("agent", "")),
            team=str(claims.get("team", "")),
            scopes=frozenset(token.scopes),
        )


_current: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "mcp_principal", default=None
)


@contextmanager
def bind_principal(principal: Principal | None) -> Iterator[None]:
    """Make ``principal`` the current caller for the enclosed block."""
    token = _current.set(principal)
    try:
        with structlog.contextvars.bound_contextvars(
            key_id=principal.key_id if principal else None,
            agent=principal.agent if principal else None,
            team=principal.team if principal else None,
        ):
            yield
    finally:
        _current.reset(token)


def current_principal() -> Principal | None:
    """Return the caller of the current request, if authenticated."""
    return _current.get()


def principal_from_scope(scope: AsgiScope | None) -> Principal | None:
    """Read the principal the SDK's authentication middleware stored in the ASGI scope."""
    user = (scope or {}).get("user")
    if isinstance(user, AuthenticatedUser):
        return Principal.from_access_token(user.access_token)
    return None


class TokenBucket:
    """In-memory token bucket per ``key_id`` (per pod; enough for low load, see ADR 0006)."""

    def __init__(self, per_minute: int, burst: int) -> None:
        self.rate = per_minute / 60.0
        self.burst = float(burst)
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        """Consume one token for ``key``; return ``False`` when the bucket is empty."""
        now = time.monotonic() if now is None else now
        tokens, last = self._state.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1:
            self._state[key] = (tokens, now)
            return False
        self._state[key] = (tokens - 1, now)
        return True


class RateLimitMiddleware:
    """Answer 429 when an authenticated key exceeds its rate [BSI KONF.15.3].

    Sits behind the SDK's ``RequireAuthMiddleware``, so every request here is authenticated.
    """

    def __init__(self, app: ASGIApp, bucket: TokenBucket) -> None:
        self.app = app
        self.bucket = bucket

    async def __call__(self, scope: AsgiScope, receive: Receive, send: Send) -> None:
        """Handle one ASGI request."""
        principal = principal_from_scope(scope)
        if principal is not None and not self.bucket.allow(principal.key_id):
            RATE_LIMITED.inc()
            log.warning("rate_limited", key_id=principal.key_id, team=principal.team)
            body = json.dumps({"error": "rate_limited"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"retry-after", b"5"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
