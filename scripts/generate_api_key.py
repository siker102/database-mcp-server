"""Generate an API key and the matching entry for the key file.

Usage:
    uv run python scripts/generate_api_key.py --agent report-bot --team analytics \
        --scopes read,pii --days 90 [--append keys.json]

The token is printed once to stderr and never stored; only the salted hash goes into the file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from database_mcp.auth import MAX_LIFETIME, Scope, entry_to_json, generate_key


def main() -> int:
    """Parse arguments, create the key, print or append the entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", required=True, help="Technical consumer the key belongs to.")
    parser.add_argument("--team", required=True, help="Team that owns the agent.")
    parser.add_argument("--scopes", required=True, help="Comma-separated: read,pii,sql")
    parser.add_argument("--days", type=int, default=None, help="Lifetime (max 90 with pii).")
    parser.add_argument("--append", type=Path, help="Key file to append the entry to.")
    args = parser.parse_args()

    scopes = {Scope(s.strip()) for s in args.scopes.split(",") if s.strip()}
    max_days = MAX_LIFETIME[Scope.PII in scopes].days
    days = args.days or max_days
    if not 1 <= days <= max_days:
        parser.error(f"--days must be between 1 and {max_days} for these scopes")

    key = generate_key(agent=args.agent, team=args.team, scopes=scopes, days=days)
    entry = entry_to_json(key.entry)
    if args.append:
        entries = json.loads(args.append.read_text()) if args.append.exists() else []
        entries.append(entry)
        args.append.write_text(json.dumps(entries, indent=2) + "\n")
    else:
        sys.stdout.write(json.dumps(entry, indent=2) + "\n")
    sys.stderr.write(f"API key (shown once, store it securely): {key.token}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
