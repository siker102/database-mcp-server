"""Logging, audit events and Prometheus metrics.

Standard interfaces instead of a product binding: JSON lines on stdout (any Kubernetes log
pipeline collects them) and a Prometheus endpoint [BSI SYS.1.6.A7]. Audit events are ordinary log
lines with ``event="audit"`` so the log platform can route them to a separate, protected index
[BSI DET.3.5].
"""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Mapping
from typing import Any

import structlog
from prometheus_client import Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TOOL_CALLS = Counter(
    "tool_calls_total", "MCP tool calls by tool, outcome and team.", ["tool", "outcome", "team"]
)
TOOL_DURATION = Histogram("tool_duration_seconds", "Duration of MCP tool calls.", ["tool"])
AUTH_FAILURES = Counter("auth_failures_total", "Rejected API keys (unknown, wrong, expired).")
RATE_LIMITED = Counter("rate_limited_total", "Requests rejected by the rate limit.")

MASK = "<redacted>"


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to write one JSON object per line to stdout.

    The standard library logging (uvicorn, the MCP SDK) is routed through the same renderer so
    every line in the output has the same format.
    """
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=[*shared, structlog.stdlib.add_logger_name],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def mask_parameters(params: Mapping[str, Any], sensitive: frozenset[str]) -> dict[str, Any]:
    """Return a copy of tool parameters with personal data replaced [BSI DET.3.3].

    Only the length is kept, which is enough to debug a request without storing the name.
    """
    masked: dict[str, Any] = {}
    for key, value in params.items():
        if key in sensitive and value is not None:
            masked[key] = f"{MASK} ({len(str(value))} chars)"
        else:
            masked[key] = value
    return masked


def audit(
    *,
    tool: str,
    outcome: str,
    params: Mapping[str, Any],
    rows: int | None,
    duration_ms: float,
) -> None:
    """Write one audit event. Identity fields come from the bound request context.

    Never pass result data here: the audit log documents *who asked what*, not the answer.
    """
    structlog.get_logger("audit").info(
        "audit",
        tool=tool,
        outcome=outcome,
        params=dict(params),
        rows=rows,
        duration_ms=round(duration_ms, 1),
    )


class RequestContextMiddleware:
    """Bind a fresh ``request_id`` and the client IP to every log line of an HTTP request.

    The client IP comes from the header the ingress sets; it is only logged, never trusted for
    access decisions (headers are client-controlled input).
    """

    def __init__(self, app: ASGIApp, client_ip_header: str = "x-forwarded-for") -> None:
        self.app = app
        self.client_ip_header = client_ip_header.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI request."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        forwarded = next((v for k, v in scope["headers"] if k == self.client_ip_header), None)
        client_ip = (
            forwarded.decode("latin-1").split(",")[0].strip()
            if forwarded
            else (scope.get("client") or ("unknown",))[0]
        )

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                # No caching of API answers and no MIME sniffing [BSI KONF.12.1.10].
                headers.append((b"cache-control", b"no-store"))
                headers.append((b"x-content-type-options", b"nosniff"))
                message["headers"] = headers
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id, client_ip=client_ip):
            await self.app(scope, receive, send_with_id)
