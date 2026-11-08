"""Shared helpers for tools: scope check, audit, metrics, error mapping."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import structlog
from mcp.server.mcpserver.exceptions import ToolError

from titanic_mcp.auth import Principal, Scope, current_principal
from titanic_mcp.observability import TOOL_CALLS, TOOL_DURATION, audit, mask_parameters
from titanic_mcp.services.passengers import ServiceError
from titanic_mcp.services.sql_guard import SqlRejectedError
from titanic_mcp.services.sql_runner import SqlExecutionError

log = structlog.get_logger(__name__)

TOOL_SCOPES: dict[str, Scope] = {
    "describe_dataset": Scope.READ,
    "search_passengers": Scope.READ,
    "get_passenger": Scope.READ,
    "get_statistics": Scope.READ,
    "run_sql": Scope.SQL,
}
"""Scope each tool requires; tools not listed here are never offered (default deny)."""

SENSITIVE_PARAMS = frozenset({"name_contains"})
"""Parameters that carry personal data and are masked in the audit log [BSI DET.3.3]."""

_EXPECTED_ERRORS = (ServiceError, SqlRejectedError, SqlExecutionError)


def require(tool: str) -> Principal:
    """Return the caller if it holds the scope ``tool`` needs, otherwise raise ``ToolError``."""
    principal = current_principal()
    if principal is None:
        raise ToolError("Authentication required.")
    needed = TOOL_SCOPES.get(tool)
    if needed is None or not principal.has(needed):
        log.warning("scope_denied", tool=tool, required=needed.value if needed else None)
        raise ToolError(f"Access denied: tool '{tool}' needs the '{needed}' scope.")
    return principal


def _row_count(result: Any) -> int | None:
    """Number of data rows handed to the agent (for the audit event, never the rows)."""
    for attr in ("passengers", "rows"):
        value = getattr(result, attr, None)
        if isinstance(value, list):
            return len(value)
    return 1 if hasattr(result, "passenger") else None


async def run_tool[T](
    tool: str, params: Mapping[str, Any], call: Callable[[Principal], Awaitable[T]]
) -> T:
    """Run one tool call with scope check, audit event and metrics.

    Expected errors become a ``ToolError`` with a helpful message (returned to the agent with
    ``isError``); anything else is logged here and reaches the agent only as a generic error
    from the SDK, without stack trace or internals [BSI DEV.3.2, DEV.3.3].
    """
    start = time.perf_counter()
    principal = current_principal()
    team = principal.team if principal else "anonymous"
    outcome = "error"
    rows: int | None = None
    try:
        checked = require(tool)
        result = await call(checked)
        outcome, rows = "ok", _row_count(result)
        return result
    except ToolError:
        outcome = "denied"
        raise
    except _EXPECTED_ERRORS as exc:
        outcome = "rejected"
        raise ToolError(str(exc)) from exc
    except Exception:
        log.exception("tool_failed", tool=tool)
        raise
    finally:
        duration = time.perf_counter() - start
        TOOL_CALLS.labels(tool=tool, outcome=outcome, team=team).inc()
        TOOL_DURATION.labels(tool=tool).observe(duration)
        audit(
            tool=tool,
            outcome=outcome,
            params=mask_parameters(params, SENSITIVE_PARAMS),
            rows=rows,
            duration_ms=duration * 1000,
        )
