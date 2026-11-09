"""Guided live demo against a running server (start the Compose stack with ``make up`` first).

Usage:
    uv run python scripts/demo.py [--url URL] [--key-file secrets/demo_api_key]

Shows, step by step: a call without key is rejected, a real question answered by get_statistics,
a valid free SQL query, and attacks on free SQL that are rejected with a reason.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

DEFAULT_KEY_FILE = Path(__file__).resolve().parents[1] / "secrets" / "demo_api_key"

ATTACKS = {
    "Daten löschen": "DELETE FROM passengers_public",
    "PII-Tabelle lesen": "SELECT name FROM passengers",
    "Server ausbremsen": "SELECT pg_sleep(10)",
    "CTE-Umgehung (Kreuzprüfung)": (
        "SELECT * FROM (WITH pg_roles AS (SELECT 1) SELECT 1) x, pg_roles"
    ),
    "Katalog per Cast abfragen": "SELECT 'passengers'::regclass",
}


def _step(title: str) -> None:
    print(f"\n=== {title} ===")


def _text(result: Any) -> str:
    return str(result.content[0].text) if result.content else ""


async def run(url: str, key: str) -> None:
    """Run the demo steps and print the results."""
    _step("1. Aufruf ohne API-Key")
    async with httpx2.AsyncClient(timeout=30) as anonymous:
        response = await anonymous.post(url, json={})
    challenge = response.headers.get("www-authenticate")
    print(f"HTTP {response.status_code}, WWW-Authenticate: {challenge}")

    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30)
    async with Client(streamable_http_client(url, http_client=http)) as client:
        _step("2. Welche Tools sieht dieser Agent?")
        print(", ".join(sorted(t.name for t in (await client.list_tools()).tools)))

        _step("3. Frage: Überlebensrate nach Klasse und Geschlecht (get_statistics)")
        stats = await client.call_tool("get_statistics", {"group_by": ["pclass", "sex"]})
        for row in (stats.structured_content or {}).get("rows", []):
            group = row["group"]
            print(
                f"Klasse {group['pclass']}  {group['sex']:<6}  "
                f"Anzahl {row['count']:>3}  Überlebensrate {row['survival_rate']:.0%}"
            )

        _step("4. Freies SQL (run_sql) auf der View ohne PII")
        query = (
            "SELECT embarked, count(*) AS n FROM passengers_public "
            "GROUP BY embarked ORDER BY n DESC"
        )
        ok = await client.call_tool("run_sql", {"query": query})
        print(query)
        print((ok.structured_content or {}).get("rows"))

        _step("5. Angriffe auf freies SQL")
        for label, attack in ATTACKS.items():
            result = await client.call_tool("run_sql", {"query": attack})
            verdict = "ABGELEHNT" if result.is_error else "DURCHGELASSEN"
            print(f"{label:<30} {verdict}: {_text(result) if result.is_error else ''}")

    _step("6. Audit-Log dieser Aufrufe")
    print('docker compose logs server | grep \'"event": "audit"\'')


def main() -> int:
    """Parse arguments and run the demo."""
    parser = argparse.ArgumentParser(description="MCP live demo")
    parser.add_argument("--url", default="http://localhost:8000/mcp")
    parser.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.key_file.read_text().strip()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
