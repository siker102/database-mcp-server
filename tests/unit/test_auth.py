from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from titanic_mcp.auth import (
    ApiKeyEntry,
    ApiKeyVerifier,
    KeyConfigError,
    Principal,
    RateLimitMiddleware,
    Scope,
    TokenBucket,
    bind_principal,
    current_principal,
    entry_to_json,
    generate_key,
    hash_secret,
    load_keys,
)

pytestmark = pytest.mark.anyio


def _write(path: Path, entries: list[ApiKeyEntry]) -> Path:
    path.write_text(json.dumps([entry_to_json(e) for e in entries]))
    return path


def test_generated_key_matches_its_entry() -> None:
    key = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=30)
    key_id, secret = key.token.removeprefix("tmcp_").split("_", 1)
    assert key_id == key.entry.key_id
    assert hash_secret(key.entry.salt, secret) == key.entry.sha256
    assert len(secret) >= 43  # 32 bytes, url-safe base64
    assert key.token not in json.dumps(entry_to_json(key.entry))


def test_two_keys_have_different_salts() -> None:
    a = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=1)
    b = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=1)
    assert a.entry.salt != b.entry.salt


def test_load_keys_roundtrip(tmp_path: Path) -> None:
    key = generate_key(agent="bot", team="a", scopes={Scope.READ, Scope.SQL}, days=365)
    keys = load_keys(_write(tmp_path / "k.json", [key.entry]))
    assert keys[key.entry.key_id] == key.entry


@pytest.mark.parametrize(("scopes", "days"), [({Scope.PII}, 91), ({Scope.READ}, 366)])
def test_too_long_lifetime_prevents_start(tmp_path: Path, scopes: set[Scope], days: int) -> None:
    key = generate_key(agent="bot", team="a", scopes=scopes, days=days)
    with pytest.raises(KeyConfigError, match="expires later"):
        load_keys(_write(tmp_path / "k.json", [key.entry]))


def test_duplicate_key_id_rejected(tmp_path: Path) -> None:
    key = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=1)
    with pytest.raises(KeyConfigError, match="Duplicate"):
        load_keys(_write(tmp_path / "k.json", [key.entry, key.entry]))


@pytest.mark.parametrize("content", ["not json", '[{"key_id": "x"}]', "{}"])
def test_invalid_key_file(tmp_path: Path, content: str) -> None:
    path = tmp_path / "k.json"
    path.write_text(content)
    with pytest.raises(KeyConfigError):
        load_keys(path)


def test_missing_key_file(tmp_path: Path) -> None:
    with pytest.raises(KeyConfigError):
        load_keys(tmp_path / "missing.json")


def test_naive_expiry_rejected() -> None:
    key = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=1)
    data = entry_to_json(key.entry)
    data["expires_at"] = "2030-01-01T00:00:00"
    with pytest.raises(ValueError, match="timezone"):
        ApiKeyEntry.model_validate(data)


async def test_verifier_accepts_valid_key() -> None:
    key = generate_key(agent="bot", team="analytics", scopes={Scope.READ, Scope.PII}, days=10)
    token = await ApiKeyVerifier({key.entry.key_id: key.entry}).verify_token(key.token)
    assert token is not None
    assert token.client_id == key.entry.key_id
    assert key.token not in token.token
    principal = Principal.from_access_token(token)
    assert principal.team == "analytics"
    assert principal.agent == "bot"
    assert principal.has(Scope.PII)
    assert not principal.has(Scope.SQL)


async def test_verifier_rejects_wrong_unknown_and_expired() -> None:
    key = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=10)
    expired = generate_key(
        agent="bot",
        team="a",
        scopes={Scope.READ},
        days=1,
        now=datetime.now(UTC) - timedelta(days=2),
    )
    verifier = ApiKeyVerifier({key.entry.key_id: key.entry, expired.entry.key_id: expired.entry})
    wrong_secret = key.token[:-4] + "AAAA"
    other = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=10)
    for token in [wrong_secret, other.token, "garbage", "", expired.token]:
        assert await verifier.verify_token(token) is None


def test_token_bucket_limits_and_refills() -> None:
    bucket = TokenBucket(per_minute=60, burst=2)
    assert bucket.allow("k", now=0.0)
    assert bucket.allow("k", now=0.0)
    assert not bucket.allow("k", now=0.0)
    assert bucket.allow("other", now=0.0)
    assert bucket.allow("k", now=1.0)  # one token per second


def test_bind_principal_sets_and_resets() -> None:
    principal = Principal(key_id="k", agent="a", team="t", scopes=frozenset({"read"}))
    assert current_principal() is None
    with bind_principal(principal):
        assert current_principal() == principal
    assert current_principal() is None


async def test_rate_limit_middleware_returns_429() -> None:
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    calls: list[str] = []

    async def app(scope: object, receive: object, send: object) -> None:
        calls.append("app")

    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    async def receive() -> dict[str, object]:
        return {}

    user = AuthenticatedUser(
        AccessToken(token="t", client_id="k", scopes=["read"], claims={"team": "t", "agent": "a"})
    )
    middleware = RateLimitMiddleware(app, TokenBucket(per_minute=1, burst=1))  # type: ignore[arg-type]
    scope = {"type": "http", "user": user}
    await middleware(scope, receive, send)  # type: ignore[arg-type]
    await middleware(scope, receive, send)  # type: ignore[arg-type]
    assert calls == ["app"]
    assert sent[0]["status"] == 429
