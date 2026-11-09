"""The whole server over HTTP with the SDK's MCP client: auth, scopes, tools, audit, metrics."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pytest
import structlog
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from prometheus_client import REGISTRY
from starlette.applications import Starlette

from database_mcp.app import create_app
from database_mcp.auth import Scope, entry_to_json, generate_key
from database_mcp.config import ServerSettings
from tests.conftest import APP, SQL, Database

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

BASE = "http://localhost:8000"


@dataclass
class Server:
    app: Starlette
    tokens: dict[str, str]

    def http(self, token: str | None) -> httpx2.AsyncClient:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app), base_url=BASE, headers=headers
        )

    def client(self, who: str, mode: str = "auto") -> Client:
        http = self.http(self.tokens[who])
        return Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode=mode)


def _make_keys(path: Path) -> dict[str, str]:
    specs = {
        "read": {Scope.READ},
        "pii": {Scope.READ, Scope.PII},
        "sql": {Scope.SQL},
        "all": {Scope.READ, Scope.PII, Scope.SQL},
        "pii_only": {Scope.PII},  # pii without read must grant nothing
    }
    tokens, entries = {}, []
    for name, scopes in specs.items():
        key = generate_key(agent=f"{name}-bot", team=f"team-{name}", scopes=scopes, days=30)
        tokens[name] = key.token
        entries.append(entry_to_json(key.entry))
    path.write_text(json.dumps(entries))
    return tokens


def _settings(keys_file: Path, **overrides: Any) -> ServerSettings:
    values: dict[str, Any] = {
        "db_app_password": APP[1],
        "db_sql_password": SQL[1],
        "api_keys_file": keys_file,
        **overrides,
    }
    return ServerSettings(**values)


@asynccontextmanager
async def running(app: Starlette) -> AsyncIterator[None]:
    async with app.router.lifespan_context(app):
        yield


@pytest.fixture
async def server(database: Database, tmp_path: Path) -> AsyncIterator[Server]:
    tokens = _make_keys(tmp_path / "keys.json")
    app = create_app(_settings(tmp_path / "keys.json"), database.settings)
    async with running(app):
        yield Server(app, tokens)


def _names(result: Any) -> set[str]:
    return {t.name for t in result.tools}


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        ("read", {"describe_dataset", "search_passengers", "get_passenger", "get_statistics"}),
        ("sql", {"run_sql"}),
        (
            "all",
            {"describe_dataset", "search_passengers", "get_passenger", "get_statistics", "run_sql"},
        ),
    ],
)
async def test_tools_list_shows_only_permitted_tools(
    server: Server, who: str, expected: set[str]
) -> None:
    async with server.client(who) as client:
        assert _names(await client.list_tools()) == expected


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_modern_and_legacy_protocol_work(server: Server, mode: str) -> None:
    """Clients on the 2026-07-28 revision and on the older handshake revisions are served."""
    async with server.client("read", mode=mode) as client:
        result = await client.call_tool("get_statistics", {"group_by": ["pclass"]})
    assert not result.is_error
    rows = result.structured_content["rows"]
    assert [r["group"]["pclass"] for r in rows] == [1, 2, 3]
    assert sum(r["count"] for r in rows) == 891


async def test_pii_only_with_pii_scope(server: Server) -> None:
    async with server.client("read") as client:
        public = await client.call_tool("get_passenger", {"passenger_id": 1})
        denied = await client.call_tool(
            "search_passengers", {"name_contains": "Braund", "limit": 1}
        )
    async with server.client("pii") as client:
        full = await client.call_tool("get_passenger", {"passenger_id": 1})
        found = await client.call_tool("search_passengers", {"name_contains": "braund", "limit": 5})
    assert "name" not in public.structured_content["passenger"]
    assert public.structured_content["passenger"]["fare"] == 7.25
    assert denied.is_error and "pii" in denied.content[0].text
    assert full.structured_content["passenger"]["name"] == "Braund, Mr. Owen Harris"
    assert found.structured_content["total"] == 2


async def test_search_filters_and_pagination(server: Server) -> None:
    async with server.client("read") as client:
        result = await client.call_tool(
            "search_passengers", {"sex": "female", "pclass": 1, "limit": 5, "offset": 5}
        )
    data = result.structured_content
    assert data["total"] == 94
    assert len(data["passengers"]) == 5
    assert all(p["sex"] == "female" and p["pclass"] == 1 for p in data["passengers"])


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_passengers", {"limit": 1000}),
        ("get_passenger", {"passenger_id": 0}),
        ("get_statistics", {"group_by": ["name"]}),
    ],
)
async def test_invalid_input_returns_tool_error(
    server: Server, tool: str, args: dict[str, Any]
) -> None:
    async with server.client("read") as client:
        result = await client.call_tool(tool, args)
    assert result.is_error


async def test_unknown_passenger_is_a_helpful_error(server: Server) -> None:
    async with server.client("read") as client:
        result = await client.call_tool("get_passenger", {"passenger_id": 5000})
    assert result.is_error and "No passenger" in result.content[0].text


async def test_scope_is_enforced_even_if_tool_is_called_directly(server: Server) -> None:
    """Hiding a tool in tools/list is not the protection; the call itself is checked."""
    async with server.client("read") as client:
        result = await client.call_tool("run_sql", {"query": "SELECT 1"})
    assert result.is_error and "sql" in result.content[0].text


async def test_describe_dataset(server: Server) -> None:
    async with server.client("read") as client:
        result = await client.call_tool("describe_dataset", {})
    columns = {c["name"]: c for c in result.structured_content["columns"]}
    assert columns["name"]["personal_data"] is True
    assert columns["fare"]["personal_data"] is False


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Bearer dbmcp_abcdefgh_" + "x" * 43])
async def test_missing_or_wrong_key_is_401(server: Server, header: str | None) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=server.app), base_url=BASE
    ) as http:
        headers = {"Authorization": header} if header else {}
        response = await http.post("/mcp", json={}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert "dbmcp_" not in response.text


async def test_health_endpoints_need_no_key(server: Server) -> None:
    async with server.http(None) as http:
        assert (await http.get("/healthz")).json() == {"status": "ok"}
        ready = await http.get("/readyz")
    assert ready.status_code == 200
    assert ready.headers["x-content-type-options"] == "nosniff"
    assert "x-request-id" in ready.headers


async def test_readyz_reports_unavailable_database(database: Database, tmp_path: Path) -> None:
    tokens = _make_keys(tmp_path / "keys.json")
    broken = database.settings.model_copy(update={"port": 1})
    app = create_app(_settings(tmp_path / "keys.json"), broken)
    async with running(app), Server(app, tokens).http(None) as http:
        response = await http.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


async def test_wrong_host_header_is_rejected(server: Server) -> None:
    """DNS-rebinding protection of the SDK: only configured Host headers are served."""
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=server.app),
        base_url="http://evil.example",
        headers={"Authorization": f"Bearer {server.tokens['read']}"},
    ) as http:
        response = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 421


async def test_rate_limit_answers_429(database: Database, tmp_path: Path) -> None:
    tokens = _make_keys(tmp_path / "keys.json")
    settings = _settings(tmp_path / "keys.json", rate_limit_burst=1, rate_limit_per_minute=1)
    app = create_app(settings, database.settings)
    async with running(app), Server(app, tokens).http(tokens["read"]) as http:
        statuses = [(await http.post("/mcp", json={})).status_code for _ in range(3)]
    assert 429 in statuses


async def test_audit_event_without_result_data_and_masked_pii(server: Server) -> None:
    with structlog.testing.capture_logs() as logs:
        async with server.client("pii") as client:
            await client.call_tool("search_passengers", {"name_contains": "Braund", "limit": 1})
    audits = [e for e in logs if e["event"] == "audit"]
    assert len(audits) == 1
    event = audits[0]
    assert event["tool"] == "search_passengers"
    assert event["outcome"] == "ok"
    assert event["rows"] == 1
    assert "Braund" not in json.dumps(event)
    assert event["params"]["name_contains"].startswith("<redacted>")
    auth = [e for e in logs if e["event"] == "auth_success"]
    assert auth and server.tokens["pii"] not in json.dumps(logs)


async def test_metrics_count_calls(server: Server) -> None:
    def value() -> float:
        sample = REGISTRY.get_sample_value(
            "tool_calls_total", {"tool": "describe_dataset", "outcome": "ok", "team": "team-read"}
        )
        return sample or 0.0

    before = value()
    async with server.client("read") as client:
        await client.call_tool("describe_dataset", {})
    assert value() == before + 1


# ---- cross-checks: scopes, PII, input bounds, errors, headers --------------------------------

TOOL_CALLS = {
    "describe_dataset": {},
    "search_passengers": {"limit": 1},
    "get_passenger": {"passenger_id": 1},
    "get_statistics": {},
    "run_sql": {"query": "SELECT 1"},
}
FIXED_TOOLS = {"describe_dataset", "search_passengers", "get_passenger", "get_statistics"}
ALLOWED = {
    "read": FIXED_TOOLS,
    "pii": FIXED_TOOLS,
    "sql": {"run_sql"},
    "all": FIXED_TOOLS | {"run_sql"},
    "pii_only": set(),
}


@pytest.mark.parametrize("who", list(ALLOWED))
async def test_scope_matrix_for_every_tool(server: Server, who: str) -> None:
    """Every tool against every key: allowed exactly where the scope permits [BSI BER.4.1]."""
    async with server.client(who) as client:
        listed = _names(await client.list_tools())
        outcomes = {
            tool: not (await client.call_tool(tool, args)).is_error
            for tool, args in TOOL_CALLS.items()
        }
    assert listed == ALLOWED[who]
    assert {tool for tool, ok in outcomes.items() if ok} == ALLOWED[who]


PII_FIELDS = {"name", "ticket", "cabin"}


def _text(result: Any) -> str:
    return " ".join(c.text for c in result.content if hasattr(c, "text"))


async def test_pii_fields_are_absent_not_just_empty_without_pii(server: Server) -> None:
    """Without pii the personal fields are not in the answer at all, neither in the structured
    result nor in the text content the SDK sends alongside [ADR 0005]."""
    async with server.client("read") as client:
        search = await client.call_tool("search_passengers", {"limit": 5})
        single = await client.call_tool("get_passenger", {"passenger_id": 2})
    for passenger in search.structured_content["passengers"]:
        assert not PII_FIELDS & passenger.keys()
    assert not PII_FIELDS & single.structured_content["passenger"].keys()
    text = _text(search) + _text(single)
    for value in ["Braund", "A/5 21171", "Cumings", "C85"]:  # name, ticket, cabin of rows 1-2
        assert value not in text


async def test_pii_key_gets_all_personal_fields(server: Server) -> None:
    async with server.client("pii") as client:
        single = await client.call_tool("get_passenger", {"passenger_id": 2})
    passenger = single.structured_content["passenger"]
    assert passenger.keys() >= PII_FIELDS
    assert passenger["cabin"] == "C85"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_passengers", {"limit": 0}),
        ("search_passengers", {"limit": -1}),
        ("search_passengers", {"limit": 101}),
        ("search_passengers", {"offset": -1}),
        ("search_passengers", {"offset": 10_001}),
        ("search_passengers", {"pclass": 4}),
        ("search_passengers", {"survived": 2}),
        ("search_passengers", {"sex": "robot"}),
        ("search_passengers", {"embarked": "X"}),
        ("search_passengers", {"min_age": -1}),
        ("search_passengers", {"max_fare": 1e9}),
        ("search_passengers", {"limit": "ten"}),
        ("get_passenger", {"passenger_id": -1}),
        ("get_passenger", {"passenger_id": 100_001}),
        ("get_passenger", {}),
        ("get_statistics", {"group_by": ["pclass", "pclass"]}),
        ("get_statistics", {"group_by": ["pclass", "sex", "embarked", "survived", "pclass"]}),
        ("get_statistics", {"group_by": "pclass; DROP TABLE passengers"}),
        ("run_sql", {"query": ""}),
        ("run_sql", {"query": "SELECT 1", "limit": 0}),
        ("run_sql", {"query": "SELECT 1", "limit": 501}),
        ("run_sql", {"query": "SELECT " + "1" * 5001}),
    ],
)
async def test_out_of_range_input_is_a_tool_error(
    server: Server, tool: str, args: dict[str, Any]
) -> None:
    """Bounds are enforced server-side before any database access [BSI DEV.2.6.1]."""
    async with server.client("all") as client:
        result = await client.call_tool(tool, args)
    assert result.is_error


@pytest.mark.parametrize("name_contains", ["a", "x" * 51, "x" * 100_000])
async def test_name_search_bounds(server: Server, name_contains: str) -> None:
    async with server.client("pii") as client:
        result = await client.call_tool("search_passengers", {"name_contains": name_contains})
    assert result.is_error


@pytest.mark.parametrize(
    ("args", "expected_total"),
    [
        ({"limit": 100}, 891),
        ({"offset": 10_000}, 891),
        ({"name_contains": "%%"}, 0),  # LIKE wildcards are escaped, not interpreted
        ({"name_contains": "__"}, 0),
    ],
)
async def test_boundary_values_are_accepted(
    server: Server, args: dict[str, Any], expected_total: int
) -> None:
    async with server.client("pii") as client:
        result = await client.call_tool("search_passengers", args)
    assert not result.is_error
    assert result.structured_content["total"] == expected_total


async def test_unexpected_error_reveals_no_internals(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected exception reaches the agent as a generic error: no message, no stack trace,
    no paths [BSI DEV.3.2, DEV.3.3]. It is logged on the server instead."""
    from database_mcp.services.passengers import PassengerService

    async def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("internal detail /srv/secret/path password=hunter2")

    monkeypatch.setattr(PassengerService, "get", explode)
    tokens = _make_keys(tmp_path / "keys.json")
    app = create_app(_settings(tmp_path / "keys.json"), database.settings)
    with structlog.testing.capture_logs() as logs:
        async with running(app), Server(app, tokens).client("read") as client:
            result = await client.call_tool("get_passenger", {"passenger_id": 1})
    text = _text(result)
    assert result.is_error
    for leak in ["internal detail", "/srv/secret", "hunter2", "Traceback", "RuntimeError"]:
        assert leak not in text
    assert any(e["event"] == "tool_failed" for e in logs)


async def test_database_error_message_reveals_no_internals(server: Server) -> None:
    async with server.client("sql") as client:
        result = await client.call_tool("run_sql", {"query": "SELECT 1 / 0"})
    text = _text(result)
    assert result.is_error
    for leak in ["Traceback", "asyncpg", "sqlalchemy", "titanic_sql", 'File "']:
        assert leak not in text


async def test_audit_of_run_sql_has_query_but_no_result_and_no_key(server: Server) -> None:
    with structlog.testing.capture_logs() as logs:
        async with server.client("sql") as client:
            result = await client.call_tool(
                "run_sql", {"query": "SELECT min(sex) AS s FROM passengers_public"}
            )
    assert result.structured_content["rows"] == [["female"]]
    audit = next(e for e in logs if e["event"] == "audit")
    assert audit["params"]["query"].startswith("SELECT min(sex)")
    assert audit["rows"] == 1
    assert "female" not in json.dumps(audit)  # the result value
    assert server.tokens["sql"] not in json.dumps(logs)


async def test_failed_auth_logs_key_id_but_never_the_key(server: Server) -> None:
    wrong = server.tokens["read"][:-5] + "AAAAA"
    with structlog.testing.capture_logs() as logs:
        async with server.http(wrong) as http:
            response = await http.post("/mcp", json={})
    assert response.status_code == 401
    failures = [e for e in logs if e["event"] == "auth_failure"]
    assert failures and failures[0]["reason"] == "wrong_secret"
    secret_part = wrong.split("_", 2)[2]
    assert secret_part not in json.dumps(logs)


TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
ACCEPT = "application/json, text/event-stream"


@pytest.mark.parametrize(
    ("template", "authenticated"),
    [
        ("Bearer {key}", True),
        ("bearer {key}", True),  # the scheme is case-insensitive (RFC 6750)
        ("Bearer  {key}", False),  # two spaces: the token then starts with a space
        ("Basic {key}", False),
        ("{key}", False),  # no scheme
        ("Bearer", False),
        ("Bearer ", False),
        ("Bearer {key} extra", False),
        ("Bearer {key}x", False),
    ],
)
async def test_authorization_header_variants(
    server: Server, template: str, authenticated: bool
) -> None:
    header = template.format(key=server.tokens["read"])
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=server.app), base_url=BASE
    ) as http:
        response = await http.post(
            "/mcp", json=TOOLS_LIST, headers={"authorization": header, "accept": ACCEPT}
        )
    assert (response.status_code != 401) is authenticated


async def test_foreign_origin_is_rejected(server: Server) -> None:
    """Browsers send Origin; only configured origins are served (DNS-rebinding protection)."""
    async with server.http(server.tokens["read"]) as http:
        response = await http.post(
            "/mcp", json=TOOLS_LIST, headers={"origin": "http://evil.example", "accept": ACCEPT}
        )
    assert response.status_code == 403


async def test_missing_host_header_is_rejected(server: Server) -> None:
    """A request without Host cannot be matched against the allowlist and is refused."""
    body = json.dumps(TOOLS_LIST).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8000),
        "headers": [
            (b"authorization", f"Bearer {server.tokens['read']}".encode()),
            (b"content-type", b"application/json"),
            (b"accept", ACCEPT.encode()),
            (b"content-length", str(len(body)).encode()),
        ],
    }
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await server.app(scope, receive, send)
    assert sent[0]["status"] == 421


async def test_metrics_are_not_served_on_the_mcp_port(server: Server) -> None:
    """/metrics lives on its own port (9090); the MCP port must not expose it [BSI KONF.11.7]."""
    async with server.http(None) as http:
        response = await http.get("/metrics")
    assert response.status_code == 404
