"""Deployment security invariants (task 237af77f): the container image and the K3s overlay keep the app's security
model — bind loopback only, a reverse-proxy sidecar as the only Service target, the Remote-User proxy identity, the
network restriction to Traefik, the shared hostPath, the off-by-default providers, and pod hardening.

The manifests are read as text (no YAML dependency in the test venv) and, when the `kustomize` binary is available,
rendered and re-checked end to end. The `finance homelab-init` behaviour is exercised against a temporary database.

The tracked overlay ships only neutral placeholder values (node name, data path, login, public URL, edge CIDR); a
deployment's real values live in an untracked local overlay (see deploy/README.md), so nothing private is committed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_finance import access, cli, db, prefs

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
DOCKER = DEPLOY / "docker"
BASE = DEPLOY / "k8s" / "base"
HOMELAB = DEPLOY / "k8s" / "overlays" / "homelab"

# The shared-db hostPath placeholder the tracked overlay ships (a neutral server path, not anyone's home).
HOST_DATA = "/srv/hermes-finance/data"
NODE = "homelab-node"      # the placeholder node name the tracked overlay ships
EDGE_CIDR = "192.0.2.10/32"  # RFC 5737 TEST-NET-1 placeholder for the edge in the tracked overlay
APP_PORT = "8765"          # the app's loopback port
SIDECAR_PORT = "8080"      # the only Service target


def read(p: Path) -> str:
    return p.read_text()


# --- the image -------------------------------------------------------------------------------------------------------
def test_dockerfile_is_multistage_python_312_nonroot():
    text = read(DOCKER / "Dockerfile")
    assert text.count("FROM ") >= 2, "a multi-stage build"
    assert "python:3.12" in text and "python:3.11" not in text and "python:3.13" not in text
    assert "uv sync --frozen" in text, "the locked dependency set, reproducibly"
    # pyproject's license-files = ["LICENSE"] is read when the project is built, so LICENSE must be in the build
    # context before `uv sync` installs the project (otherwise `uv sync --frozen --no-dev` fails). It is copied
    # alongside README.md, which pyproject's `readme` also references, before the first sync.
    copy_line = next(ln for ln in text.splitlines() if ln.startswith("COPY ") and "pyproject.toml" in ln)
    assert "LICENSE" in copy_line and "README.md" in copy_line, "LICENSE copied with the project metadata"
    assert text.index("LICENSE") < text.index("uv sync --frozen --no-dev\n"), "LICENSE present before the project install"
    # non-root: a fixed unprivileged UID, and USER set to it (never root)
    assert "USER 10001" in text
    assert "--extra mcp" not in text and "--extra photos" not in text, "MCP and photos stay out of the image"
    assert f"EXPOSE {APP_PORT}" in text
    assert "HEALTHCHECK" in text


def test_entrypoint_seeds_then_serves_on_loopback():
    text = read(DOCKER / "entrypoint.sh")
    assert "finance homelab-init" in text
    assert "finance serve" in text
    assert '--host "$HOST"' in text or "--host 127.0.0.1" in text
    assert "127.0.0.1" in text


def test_sidecar_proxies_loopback_and_forwards_identity():
    conf = read(BASE / "nginx.conf")
    assert f"listen {SIDECAR_PORT}" in conf
    assert f"127.0.0.1:{APP_PORT}" in conf, "the sidecar reaches the app over loopback"
    assert "proxy_set_header Host $host" in conf, "the public Host is preserved"
    assert "/healthz" in conf, "a readiness target"
    # the sidecar must NOT invent or rewrite the trusted identity header (the edge sets it)
    assert "set_header Remote-User" not in conf.replace("# ", "")


# --- the base manifests ----------------------------------------------------------------------------------------------
def test_base_deployment_is_hardened_and_single_writer():
    text = read(BASE / "deployment.yaml")
    assert "replicas: 1" in text
    assert "type: Recreate" in text
    assert "automountServiceAccountToken: false" in text
    assert text.count("readOnlyRootFilesystem: true") >= 2, "both containers"
    assert "runAsNonRoot: true" in text
    assert 'drop: ["ALL"]' in text
    assert "allowPrivilegeEscalation: false" in text
    assert "seccompProfile" in text and "RuntimeDefault" in text
    # the app is told to bind loopback; only the sidecar exposes a containerPort
    assert "127.0.0.1" in text
    assert f"containerPort: {SIDECAR_PORT}" in text
    assert f"containerPort: {APP_PORT}" not in text, "the app's port is never a Pod port"
    # providers that must never phone home
    for off in ("HERMES_PLAID", "HERMES_ALPACA", "HERMES_CHROME_DEVTOOLS"):
        assert off in text


def test_service_targets_only_the_sidecar():
    text = read(BASE / "service.yaml")
    assert "type: ClusterIP" in text, "the fail-closed default"
    assert f"port: {SIDECAR_PORT}" in text
    assert "targetPort: http" in text
    assert not any(APP_PORT in ln for ln in text.splitlines() if "port:" in ln), \
        "the Service never exposes the app's loopback port"


def test_base_networkpolicy_denies_ingress_by_default():
    text = read(BASE / "networkpolicy.yaml")
    assert "policyTypes:" in text and "Ingress" in text
    assert "ingress: []" in text, "deny all; the overlay adds the one allowed source"


# --- the homelab overlay ---------------------------------------------------------------------------------------------
def test_overlay_pins_node_shared_volume_and_identity():
    patch = read(HOMELAB / "patch-deployment.yaml")
    assert f"kubernetes.io/hostname: {NODE}" in patch
    assert f"path: {HOST_DATA}" in patch, "the shared hostPath for one finance.db"
    assert "type: Directory" in patch
    assert "FINANCE_PROXY_HEADER" in patch and "Remote-User" in patch
    assert 'value: "you"' in patch
    assert "finance.example.invalid" in patch
    # the shared host directory must not be recursively chowned (fsGroup removed in the overlay)
    km = read(HOMELAB / "kustomization.yaml")
    assert "op: remove" in km and "/securityContext/fsGroup" in km


def test_overlay_networkpolicy_allows_only_traefik():
    text = read(HOMELAB / "patch-networkpolicy.yaml")
    assert EDGE_CIDR in text
    assert f"port: {SIDECAR_PORT}" in text


def test_nodeport_is_opt_in_and_documents_fail_closed():
    text = read(HOMELAB / "service-nodeport.yaml")
    assert "NodePort" in text
    assert "NOT APPLIED BY DEFAULT" in text
    assert "builtin" in text, "the fail-closed fallback"
    km = read(HOMELAB / "kustomization.yaml")
    assert "service-nodeport.yaml" not in km.split("resources:")[1].split("patches:")[0], \
        "the NodePort must not be in the applied resources"


def test_docs_cover_edge_middleware_firewall_and_agpl():
    readme = read(DEPLOY / "README.md")
    assert "Remote-User" in readme
    assert "strip" in readme.lower() and "Remote-*" in readme, "strip inbound Remote-* at the edge"
    assert "Authelia" in readme and "forwardAuth" in readme
    assert "firewall" in readme.lower() and "192.0.2.10" in readme
    assert "builtin" in readme, "the fail-closed fallback is documented"
    assert "overlays/local" in readme, "the untracked local overlay for real values is documented"
    assert "rollout undo" in readme and "backup" in readme.lower()
    notice = read(DEPLOY / "AGPL-NETWORK-NOTICE.md")
    assert "AGPL" in notice and "Affero" in notice
    assert "source" in notice.lower()


# --- behaviour: the app and the homelab-init command -----------------------------------------------------------------
def test_proxy_mode_refuses_non_loopback_bind():
    """The security invariant the whole deployment rests on: in proxy mode the app binds loopback only."""
    acc = access.Access("proxy", "Remote-User", {"you"}, ["https://finance.example.invalid"])
    assert access.startup_problems(acc, "127.0.0.1", login_ready=False) == []
    problems = access.startup_problems(acc, "0.0.0.0", login_ready=False)
    assert problems and any("127.0.0.1" in p for p in problems)


def test_homelab_init_seeds_proxy_mode_and_turns_providers_off(data_dir):
    rc = cli.main(["homelab-init", "--allow-user", "you", "--public-url", "https://money.example.net"])
    assert rc == 0
    c = db.connect()
    try:
        assert prefs.get(c, "access.mode") == "proxy"
        assert prefs.get(c, "access.proxy_header") == "Remote-User"
        assert prefs.get(c, "access.allowed_identities") == ["you"]
        assert "https://money.example.net" in prefs.get(c, "access.public_urls")
        assert prefs.get(c, "mcp.enabled") is False
        assert prefs.get(c, "ai.mode") == "off"
        assert prefs.get(c, "valuations.carfax") is False
        assert prefs.get(c, "valuations.redfin") is False
        assert prefs.get(c, "valuations.photos") is False
        acc = access.load(c)
        assert acc.mode == "proxy" and acc.header == "Remote-User"
        # the seeded identity is admitted; another login is refused
        assert acc.identities == {"you"}
    finally:
        c.close()


def test_homelab_init_is_idempotent_and_needs_an_identity(data_dir):
    assert cli.main(["homelab-init"]) == 2, "no identity: refused"
    assert cli.main(["homelab-init", "--allow-user", "you"]) == 0
    assert cli.main(["homelab-init", "--allow-user", "you"]) == 0, "safe to run again"
    c = db.connect()
    try:
        assert prefs.get(c, "access.allowed_identities") == ["you"], "no duplicate identities"
    finally:
        c.close()


@pytest.mark.skipif(not shutil.which("kustomize"), reason="kustomize not installed")
def test_kustomize_overlay_renders_the_invariants():
    out = subprocess.run(["kustomize", "build", str(HOMELAB)], check=True, capture_output=True, text=True).stdout
    assert "namespace: finance" in out
    assert "replicas: 1" in out and "type: Recreate" in out
    assert f"path: {HOST_DATA}" in out
    assert EDGE_CIDR in out
    assert NODE in out
    assert "automountServiceAccountToken: false" in out
    assert "fsGroup" not in out, "the overlay removes fsGroup so the shared hostPath is never chowned"
    assert "type: NodePort" not in out, "the safe ClusterIP default"
