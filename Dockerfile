# syntax=docker/dockerfile:1
# Server image: multi-stage build, non-root user, read-only root filesystem compatible.
# Base images are official and pinned by digest [BSI SYS.1.6.A6, DEV.4.4].

FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first (cached layer), installed exactly as locked, with hash checks.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
# No build tools, no uv, no shell scripts in the final image: less attack surface
# [BSI KONF.10.4]. Fixed numeric UID so Kubernetes can enforce runAsNonRoot [BSI SYS.1.6.A17].
# Security updates of the base image that are newer than the pinned digest (found by Trivy),
# and no pip at runtime: it is not needed and brings its own vendored, outdated libraries.
# DL3008: deliberately unpinned, the point is to get the newest security fix; Debian removes
# superseded versions from its mirrors, so a pin would break the build.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --only-upgrade -y --no-install-recommends zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=build /opt/venv /opt/venv
COPY data/titanic.csv /app/data/titanic.csv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DBMCP_DATA_FILE=/app/data/titanic.csv
WORKDIR /app
USER 10001:10001
EXPOSE 8000 9090
ENTRYPOINT ["database-mcp"]
CMD ["serve"]
