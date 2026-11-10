#!/bin/sh
# Container entrypoint for hermes-finance in a reverse-proxied (proxy-mode) deployment.
#
# 1. Seed the database settings the deployment needs, idempotently: proxy access mode behind the trusted identity
#    header (Remote-User by default), the allowed logins and public URLs (from FINANCE_ALLOWED_USERS /
#    FINANCE_PUBLIC_URLS or the flags), and MCP, the API-key AI brain and the keyless valuation sources off.
# 2. Serve, bound to loopback only. In proxy mode the app refuses any non-loopback --host, so the same-Pod
#    reverse-proxy sidecar reaches it over 127.0.0.1 and is the only thing exposed to the Service.
#
# No secret is read or written here; credentials (if any) arrive through a mounted secrets file or the Hermes
# credential source, never through Git, a ConfigMap, an image layer, or a command argument.
set -eu

HOST="${HERMES_FINANCE_FINANCE_HOST:-127.0.0.1}"
PORT="${HERMES_FINANCE_FINANCE_PORT:-8765}"

# --proxy-header defaults to Remote-User; --allow-user / --public-url merge with the FINANCE_* environment variables.
finance homelab-init --proxy-header "${FINANCE_PROXY_HEADER:-Remote-User}"

exec finance serve --host "$HOST" --port "$PORT"
