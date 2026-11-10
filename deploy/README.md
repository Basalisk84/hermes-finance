# Deploying hermes-finance

Reproducible container and Kubernetes (K3s) artifacts for running hermes-finance behind a reverse-proxy login (for
example at `https://finance.example.invalid`) without weakening its security model. Everything here is **declarative
and secret-free**: no credential is placed in Git, a ConfigMap, an image layer, or a command argument.

> **These manifests ship placeholder values.** The node name, hostPath, allowed user, public URL and edge CIDR in
> `k8s/overlays/homelab/` are neutral examples. Supply your real deployment values in a **local, untracked overlay** —
> see [Your real values live in a local overlay](#your-real-values-live-in-a-local-overlay) below. Nothing private is
> committed here.

> **AGPL network-use notice.** hermes-finance is licensed **AGPL-3.0-or-later**. Operating a modified version over a
> network triggers the AGPL's source-availability obligation. See [`AGPL-NETWORK-NOTICE.md`](AGPL-NETWORK-NOTICE.md).

## Contents

| Path | What it is |
|---|---|
| `docker/Dockerfile` | Multi-stage image, pinned to Python 3.12, non-root (UID 10001). |
| `docker/entrypoint.sh` | Seeds proxy-mode settings idempotently, then serves bound to `127.0.0.1:8765`. |
| `k8s/base/` | Generic kustomize base: Deployment (app + reverse-proxy sidecar), Service, NetworkPolicy, sidecar ConfigMap. |
| `k8s/base/nginx.conf` | The same-Pod reverse-proxy sidecar config (the only Service target). |
| `k8s/overlays/homelab/` | Example overlay: pins the base to a node, a shared hostPath, the `Remote-User` identity, and the edge-only network restriction (all placeholder values). |

## The security model in one breath

1. The **app binds `127.0.0.1:8765` only.** In proxy mode it *refuses* any other bind, because it trusts an identity
   header that anyone reaching the port directly could forge.
2. A **reverse-proxy sidecar** in the same Pod shares the network namespace, reaches the app over loopback, and is the
   **only container the Service targets.** It forwards — it does not authenticate.
3. Trust therefore rests entirely on the **edge** (Traefik + Authelia) and on **network isolation**:
   - the edge authenticates the user, **strips any inbound `Remote-*`**, and repopulates a trusted `Remote-User`;
   - only the edge (NetworkPolicy + host firewall, below) can reach the Service.
4. The app checks the forwarded `Remote-User` against the allowlist (your allowed login) and the Host against its
   public URL. Approval cards still gate every write; this deployment broadens nothing.

```
 browser ──TLS──▶ Traefik (edge)                  K3s Pod (your node)
                   • Authelia forward-auth        ┌───────────────────────────────┐
                   • strip inbound Remote-*        │ nginx sidecar :8080  ──loop──▶ │ finance 127.0.0.1:8765
                   • set Remote-User: you  ───────▶│  (only Service target)        │ (proxy mode, Remote-User)
                                                   └───────────────────────────────┘
```

## Build the image

```sh
# From the repository root (the build context):
docker build -f deploy/docker/Dockerfile -t hermes-finance:local .
# For K3s with containerd, import it onto your node:
docker save hermes-finance:local | ssh your-node 'sudo k3s ctr images import -'
```

The build uses `uv sync --frozen` (the lockfile is authoritative) and installs **no optional extras** — `mcp` and
`photos` are deliberately absent, so MCP returns 501 and photo generation degrades at the image layer.

## Apply the manifests

```sh
kustomize build deploy/k8s/overlays/homelab | kubectl apply -f -
# or: kubectl apply -k deploy/k8s/overlays/homelab
```

Before applying, set `runAsUser`/`runAsGroup` in your overlay's `patch-deployment.yaml` to the UID/GID that owns the
shared data directory on the node (run `id -u <owner>` / `id -g <owner>`). This is what lets the host Hermes
plugin and the containerised app **share one `finance.db`** while keeping the `0700` directory / `0600` file
permissions. The overlay removes the base `fsGroup` so kubelet never recursively chowns that shared directory.

## Your real values live in a local overlay

The tracked `k8s/overlays/homelab/` overlay carries only **placeholder example values** so that nothing private is
ever committed to this public repository:

| Field | Placeholder in Git | Where it is |
|---|---|---|
| node name | `homelab-node` | `overlays/homelab/patch-deployment.yaml` (`nodeSelector`) |
| shared data dir | `/srv/hermes-finance/data` | `overlays/homelab/patch-deployment.yaml` (`hostPath.path`) |
| allowed login | `you` | `overlays/homelab/patch-deployment.yaml` (`FINANCE_ALLOWED_USERS`) |
| public URL | `https://finance.example.invalid` | `overlays/homelab/patch-deployment.yaml` (`FINANCE_PUBLIC_URLS`) |
| edge CIDR | `192.0.2.10/32` | `overlays/homelab/patch-networkpolicy.yaml` (`ipBlock.cidr`) |

Put your real values in an **untracked local overlay** that is never committed. The repository `.gitignore` ignores
`deploy/k8s/overlays/local/`, so create and apply that one:

```sh
# Start from the example overlay, then edit the copied files with your real node, path, login, URL and edge CIDR.
cp -r deploy/k8s/overlays/homelab deploy/k8s/overlays/local
$EDITOR deploy/k8s/overlays/local/patch-deployment.yaml      # node, hostPath, runAsUser/Group, login, public URL
$EDITOR deploy/k8s/overlays/local/patch-networkpolicy.yaml   # your edge CIDR

kustomize build deploy/k8s/overlays/local | kubectl apply -f -
# or: kubectl apply -k deploy/k8s/overlays/local
```

The tracked `homelab` overlay still renders on its own (with the placeholder values) so the examples and tests stay
self-contained; only your `local/` copy carries anything real, and it stays off Git.

## Required Traefik + Authelia middleware (document only — do not edit live infrastructure)

The manifests here do **not** touch Traefik or Authelia. The following is the middleware your edge must already apply
to your public router. Apply it yourself, out of band.

```yaml
# Authelia forward-auth: authenticate, then hand back the signed-in identity as Remote-User.
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata: { name: authelia, namespace: traefik }
spec:
  forwardAuth:
    address: http://authelia.authelia.svc:9091/api/verify?rd=https://auth.example.invalid
    authResponseHeaders:
      - Remote-User      # the trusted identity the app reads
      - Remote-Groups
      - Remote-Name
      - Remote-Email
---
# Strip any client-supplied Remote-* BEFORE forward-auth repopulates the trusted ones. Order matters:
# this middleware must run ahead of `authelia` in the router's middleware chain.
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata: { name: strip-remote-headers, namespace: traefik }
spec:
  headers:
    customRequestHeaders:
      Remote-User: ""
      Remote-Groups: ""
      Remote-Name: ""
      Remote-Email: ""
---
# Router (IngressRoute) wiring, for reference:
#   middlewares: [strip-remote-headers, authelia]   # strip first, then authenticate+set
#   rule: Host(`finance.example.invalid`)
```

Without the strip middleware a client could send `Remote-User: you` and be trusted. With it, inbound copies are cleared
and only Authelia's value survives.

## Network isolation: only the edge may reach the service

The `homelab` overlay's NetworkPolicy allows ingress to the sidecar (`:8080`) **only from the edge CIDR** (a placeholder
`192.0.2.10/32` in the tracked example — set your real edge address in your local overlay) and denies everything else.
The base denies all ingress.

**NodePort caveat / fail-closed.** The default Service is `ClusterIP`: an in-cluster Traefik reaches it and the
NetworkPolicy *fully governs* access — the fail-closed default. If your Traefik runs **off-cluster** (an edge off the
cluster) it must reach a NodePort, and a NodePort can **bypass pod-level NetworkPolicy** via kube-proxy DNAT.
Kubernetes alone therefore cannot *prove* that only the edge reaches it. If you enable
`overlays/homelab/service-nodeport.yaml`, you MUST also add a host-firewall rule on the node:

```sh
# Allow only the edge to the NodePort; drop everything else. (Example; adapt to your firewall and edge address.)
sudo iptables -I INPUT -p tcp --dport 30876 -s 192.0.2.10 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 30876 -j DROP
```

If you cannot guarantee that restriction, **fail closed**: do not rely on proxy mode (which trusts `Remote-User`).
Switch the app to the built-in login instead, so a forged header alone cannot get in:

```sh
# On the node, against the shared data dir (finish the login setup on a loopback-only session first):
finance access mode builtin
```

## Secrets

No secret ships in this repository. Supply Plaid/AI credentials (only if you enable those features — this deployment
leaves them off) at runtime via a mounted `secrets.env` (0600) or the Hermes credential source. Never add them to a
ConfigMap, the image, or a command argument.

## What is turned off by default here

`entrypoint.sh` runs `finance homelab-init` on every start, which idempotently sets, in the database:

| Setting | Value | Effect |
|---|---|---|
| `access.mode` | `proxy` | Behind your proxy login. |
| `access.proxy_header` | `Remote-User` | The trusted identity header. |
| `mcp.enabled` | `false` | `/mcp` returns 404 (and the image omits the `mcp` extra → 501). |
| `ai.mode` | `off` | No API-key AI brain, no live Ask answers. |
| `valuations.carfax` / `redfin` / `photos` | `false` | No Carfax, Redfin or photo fetches. |

`HERMES_PLAID=off`, `HERMES_ALPACA=off` and `HERMES_CHROME_DEVTOOLS=off` are also set in the environment.

## Pod hardening

- `runAsNonRoot`, dropped capabilities, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`.
- `readOnlyRootFilesystem: true` on both containers (writable `emptyDir` at `/tmp` only).
- `automountServiceAccountToken: false` (the app needs no Kubernetes API).
- Liveness via `finance doctor` (offline, read-only); sidecar readiness/liveness via `GET /healthz`.
- `replicas: 1`, `strategy: Recreate` — one SQLite writer, the volume lock released before the new Pod starts.

## Rollback and backup

**Backup** (a read-only byte copy, safe while the app runs — WAL-aware):

```sh
kubectl -n finance exec deploy/hermes-finance -c finance -- \
  sh -c 'finance backup --out /tmp/f.db && cat /tmp/f.db' > finance-backup.db
# or copy the whole data dir off the host directly:
ssh your-node 'sudo tar -C /srv/hermes-finance/data -czf - .' > finance-data.tgz
```

**Rollback** the Deployment to the previous image/manifest:

```sh
kubectl -n finance rollout undo deploy/hermes-finance
kubectl -n finance rollout status deploy/hermes-finance
```

Because the strategy is `Recreate` and there is a single replica, the old Pod fully stops (releasing the WAL lock)
before the new one starts, so a rollback never leaves two writers on one database. The data directory survives Pod
replacement; a schema downgrade is **not** supported — restore the backup instead if you roll back across a schema
bump.
