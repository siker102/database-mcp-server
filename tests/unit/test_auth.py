from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from database_mcp.auth import (
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
    key_id, secret = key.token.removeprefix("dbmcp_").split("_", 1)
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


# ---- edge cases of key parsing, expiry and the key file ------------------------------------

FIXED_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


class _FrozenClock(datetime):
    """``datetime`` whose ``now()`` is fixed, to test expiry exactly at the boundary."""

    @classmethod
    def now(cls, tz: object = None) -> _FrozenClock:  # type: ignore[override]
        return cls.fromtimestamp(FIXED_NOW.timestamp(), tz=UTC)


def _malformed_variants(token: str) -> list[str]:
    key_id, secret = token.removeprefix("dbmcp_").split("_", 1)
    return [
        "",
        "dbmcp_",
        f" {token}",  # what "Bearer  <key>" (two spaces) leaves after the SDK strips "Bearer "
        f"{token} ",
        f"{token}\n",
        token.upper(),  # prefix and key_id are case-sensitive
        "DBMCP_" + token.removeprefix("dbmcp_"),
        "tmcp_" + token.removeprefix("dbmcp_"),  # old prefix
        f"dbmcp_{key_id[:7]}_{secret}",  # key_id too short
        f"dbmcp_{key_id}x{'y' * 30}_{secret}",  # key_id too long
        f"dbmcp_{key_id}_{secret[:42]}",  # secret too short
        f"dbmcp_{key_id}_{secret}ä",  # character outside the alphabet
        f"dbmcp_{key_id}_{secret}_extra",  # wrong secret (extra characters)
        f"dbmcp_{key_id}",
        secret,
    ]


async def test_verifier_rejects_malformed_tokens() -> None:
    """Every malformed variant of a valid key is rejected, never partially accepted."""
    key = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=10)
    verifier = ApiKeyVerifier({key.entry.key_id: key.entry})
    assert await verifier.verify_token(key.token) is not None
    for token in _malformed_variants(key.token):
        assert await verifier.verify_token(token) is None, token


async def test_secret_of_one_key_does_not_open_another() -> None:
    """The secret is bound to its key_id via the per-key salted hash [BSI DEV.3.4]."""
    a = generate_key(agent="a", team="t", scopes={Scope.READ}, days=10)
    b = generate_key(agent="b", team="t", scopes={Scope.READ, Scope.SQL}, days=10)
    verifier = ApiKeyVerifier({a.entry.key_id: a.entry, b.entry.key_id: b.entry})
    secret_a = a.token.removeprefix("dbmcp_").split("_", 1)[1]
    assert await verifier.verify_token(f"dbmcp_{b.entry.key_id}_{secret_a}") is None


async def test_expiry_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key is valid until just before ``expires_at`` and rejected from that instant on."""
    import database_mcp.auth as auth_module

    monkeypatch.setattr(auth_module, "datetime", _FrozenClock)
    key = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=1)

    async def accepted(expires_at: datetime) -> bool:
        entry = key.entry.model_copy(update={"expires_at": expires_at})
        return await ApiKeyVerifier({entry.key_id: entry}).verify_token(key.token) is not None

    assert await accepted(FIXED_NOW + timedelta(seconds=1))
    assert not await accepted(FIXED_NOW)
    assert not await accepted(FIXED_NOW - timedelta(seconds=1))


async def test_expiry_with_other_timezone_compares_the_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """13:00+02:00 is 11:00 UTC: with "now" at 12:00 UTC the key is expired, although 13:00
    looks later than 12:00. The instant counts, not the wall-clock time."""
    import database_mcp.auth as auth_module

    monkeypatch.setattr(auth_module, "datetime", _FrozenClock)
    key = generate_key(agent="bot", team="a", scopes={Scope.READ}, days=1)
    plus_two = timezone(timedelta(hours=2))
    expired = key.entry.model_copy(
        update={"expires_at": datetime(2026, 6, 1, 13, 0, tzinfo=plus_two)}  # 11:00 UTC
    )
    valid = key.entry.model_copy(
        update={"expires_at": datetime(2026, 6, 1, 15, 0, tzinfo=plus_two)}  # 13:00 UTC
    )
    assert await ApiKeyVerifier({expired.key_id: expired}).verify_token(key.token) is None
    assert await ApiKeyVerifier({valid.key_id: valid}).verify_token(key.token) is not None


@pytest.mark.parametrize(
    ("scopes", "limit_days"),
    [({Scope.READ, Scope.PII}, 90), ({Scope.READ}, 365), ({Scope.SQL}, 365)],
)
def test_max_lifetime_boundary(tmp_path: Path, scopes: set[Scope], limit_days: int) -> None:
    """Exactly the maximum lifetime is allowed, one second more prevents the start."""
    key = generate_key(agent="bot", team="a", scopes=scopes, days=1, now=FIXED_NOW)
    at_limit = key.entry.model_copy(update={"expires_at": FIXED_NOW + timedelta(days=limit_days)})
    too_long = at_limit.model_copy(
        update={"expires_at": at_limit.expires_at + timedelta(seconds=1)}
    )
    assert load_keys(_write(tmp_path / "ok.json", [at_limit]), now=FIXED_NOW)
    with pytest.raises(KeyConfigError, match="expires later"):
        load_keys(_write(tmp_path / "long.json", [too_long]), now=FIXED_NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scopes", []),  # a key without scopes would be useless and suspicious
        ("scopes", ["read", "admin"]),  # unknown scope
        ("key_id", "ABCDEFGH"),  # upper case is not what generate_key produces
        ("key_id", "short"),
        ("salt", "zz" * 16),
        ("sha256", "00"),
        ("agent", ""),
        ("team", "x" * 65),
        ("expires_at", "not a date"),
    ],
)
def test_invalid_entry_prevents_start(tmp_path: Path, field: str, value: object) -> None:
    data = entry_to_json(generate_key(agent="b", team="t", scopes={Scope.READ}, days=1).entry)
    data[field] = value
    path = tmp_path / "k.json"
    path.write_text(json.dumps([data]))
    with pytest.raises(KeyConfigError):
        load_keys(path)


def test_unknown_field_in_entry_prevents_start(tmp_path: Path) -> None:
    """A typo such as ``scope`` instead of ``scopes`` must not be silently ignored."""
    data = entry_to_json(generate_key(agent="b", team="t", scopes={Scope.READ}, days=1).entry)
    data["secret"] = "plaintext secret does not belong here"
    path = tmp_path / "k.json"
    path.write_text(json.dumps([data]))
    with pytest.raises(KeyConfigError):
        load_keys(path)


def test_empty_key_list_loads_but_admits_nobody(tmp_path: Path) -> None:
    path = tmp_path / "k.json"
    path.write_text("[]")
    assert load_keys(path) == {}


def test_key_file_error_does_not_echo_file_content(tmp_path: Path) -> None:
    """The error names the file and the error type, not the (possibly secret) content."""
    path = tmp_path / "k.json"
    path.write_text('[{"key_id": "secretvalue-xyz"}')
    with pytest.raises(KeyConfigError) as info:
        load_keys(path)
    assert "secretvalue" not in str(info.value)


def test_token_bucket_never_exceeds_burst_and_keys_are_isolated() -> None:
    bucket = TokenBucket(per_minute=60, burst=3)
    assert all(bucket.allow("a", now=0.0) for _ in range(3))
    assert not bucket.allow("a", now=0.0)
    # a long pause refills to the burst, not beyond
    assert all(bucket.allow("a", now=10_000.0) for _ in range(3))
    assert not bucket.allow("a", now=10_000.0)
    # half a second refills half a token: still not enough for one request
    assert not bucket.allow("a", now=10_000.5)
    assert bucket.allow("a", now=10_001.0)
    # another key is unaffected by "a"
    assert all(bucket.allow("b", now=10_000.0) for _ in range(3))


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
