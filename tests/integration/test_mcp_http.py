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

from tests.conftest import APP, SQL, Database
from titanic_mcp.app import create_app
from titanic_mcp.auth import Scope, entry_to_json, generate_key
from titanic_mcp.config import ServerSettings

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


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Bearer tmcp_abcdefgh_" + "x" * 43])
async def test_missing_or_wrong_key_is_401(server: Server, header: str | None) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=server.app), base_url=BASE
    ) as http:
        headers = {"Authorization": header} if header else {}
        response = await http.post("/mcp", json={}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert "tmcp_" not in response.text


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
