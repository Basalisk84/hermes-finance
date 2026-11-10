# Reproducible, multi-stage image for hermes-finance, pinned to Python 3.12 and running non-root.
#
# Build (from the repository root):
#   docker build -f deploy/docker/Dockerfile -t hermes-finance:local .
#
# The build stage resolves the locked dependency set with uv (--frozen: uv.lock is authoritative, the build fails if it
# is stale). The runtime stage carries only the virtualenv and the application, runs as an unprivileged fixed UID, and
# binds 127.0.0.1 only (proxy mode) — a same-Pod reverse-proxy sidecar is the only thing that reaches it. MCP, the
# API-key AI brain and the keyless valuation sources are left out / turned off by the homelab overlay; see deploy/README.md.

# --- build ------------------------------------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS build

# Pinned uv (the project's build/dependency manager). Copying the static binary keeps the build hermetic.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependency layer first (cached until the lock or manifest changes). README.md is referenced by pyproject metadata.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the application itself. No optional extras: `mcp` and `photos` are deliberately absent (MCP returns 501,
# photos degrade gracefully) so the image is small and the homelab's "off by default" posture holds at the image layer.
COPY src ./src
RUN uv sync --frozen --no-dev

# --- runtime ----------------------------------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="hermes-finance" \
      org.opencontainers.image.description="Self-hosted personal-finance dashboard (AGPL-3.0-or-later)" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/Basalisk84/hermes-finance"

# A fixed, unprivileged account that will own the mounted data directory. No shell, no home writes.
RUN groupadd --gid 10001 finance \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin finance

WORKDIR /app
COPY --from=build /app /app
COPY deploy/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# The venv's console scripts (the `finance` command) go on PATH. The data directory is a mount point the app creates
# 0700 and its files 0600 (config.ensure_data_dir / db.connect enforce this, with umask 077).
ENV PATH="/app/.venv/bin:$PATH" \
    FINANCE_DATA_DIR=/data \
    HERMES_FINANCE_FINANCE_HOST=127.0.0.1 \
    HERMES_FINANCE_FINANCE_PORT=8765 \
    PYTHONUNBUFFERED=1

# Created here for the default (emptyDir / local) case; in Kubernetes the hostPath volume supplies it and fsGroup/owner
# make it writable by UID 10001.
RUN install -d -o finance -g finance -m 0700 /data

USER 10001:10001

# The app listens on loopback only; nothing outside the Pod's network namespace should target this port directly.
EXPOSE 8765

# finance doctor is offline and read-only (exit 0 when nothing is missing) — a cheap liveness signal.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD ["finance", "doctor"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
