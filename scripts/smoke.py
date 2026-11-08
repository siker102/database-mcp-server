"""End-to-end smoke test against a running server (Compose stack or a cluster via ingress).

Usage:
    uv run python scripts/smoke.py --url http://localhost:8000/mcp [--key-file secrets/demo_api_key]

Checks: tools are listed, describe_dataset / get_statistics / run_sql work, a call without key
gets 401, and a forbidden SQL statement is rejected. Exit code 0 only if everything passes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

DEFAULT_KEY_FILE = Path(__file__).resolve().parents[1] / "secrets" / "demo_api_key"


def _check(ok: bool, label: str) -> bool:
    sys.stdout.write(f"{'OK  ' if ok else 'FAIL'} {label}\n")
    return ok


async def run(url: str, key: str) -> bool:
    """Run all checks and return whether all passed."""
    results: list[bool] = []
    async with httpx2.AsyncClient(timeout=30) as anonymous:
        response = await anonymous.post(url, json={})
        results.append(_check(response.status_code == 401, "request without key -> 401"))

    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30)
    async with Client(streamable_http_client(url, http_client=http)) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        results.append(_check("describe_dataset" in tools, f"tools/list -> {sorted(tools)}"))

        described = await client.call_tool("describe_dataset", {})
        results.append(_check(not described.is_error, "describe_dataset"))

        stats = await client.call_tool("get_statistics", {"group_by": ["pclass"]})
        rows = (stats.structured_content or {}).get("rows", [])
        rates = {r["group"]["pclass"]: r["survival_rate"] for r in rows}
        results.append(_check(not stats.is_error and len(rows) == 3, f"get_statistics {rates}"))

        sql = await client.call_tool(
            "run_sql", {"query": "SELECT sex, count(*) FROM passengers_public GROUP BY sex"}
        )
        results.append(_check(not sql.is_error, f"run_sql {sql.structured_content}"))

        blocked = await client.call_tool("run_sql", {"query": "DELETE FROM passengers_public"})
        results.append(_check(blocked.is_error, "run_sql DELETE is rejected"))
    return all(results)


def main() -> int:
    """Parse arguments and run the smoke test."""
    parser = argparse.ArgumentParser(description="MCP smoke test")
    parser.add_argument("--url", default="http://localhost:8000/mcp")
    parser.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    args = parser.parse_args()
    key = args.key_file.read_text().strip()
    return 0 if asyncio.run(run(args.url, key)) else 1


if __name__ == "__main__":
    sys.exit(main())
