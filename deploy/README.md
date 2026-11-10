# Deploying hermes-finance

Reproducible container and Kubernetes (K3s) artifacts for running hermes-finance at
`https://money.basalisk.net` without weakening its security model. Everything here is **declarative and
secret-free**: no credential is placed in Git, a ConfigMap, an image layer, or a command argument.

> **AGPL network-use notice.** hermes-finance is licensed **AGPL-3.0-or-later**. Operating a modified version over a
> network triggers the AGPL's source-availability obligation. See [`AGPL-NETWORK-NOTICE.md`](AGPL-NETWORK-NOTICE.md).

## Contents

| Path | What it is |
|---|---|
| `docker/Dockerfile` | Multi-stage image, pinned to Python 3.12, non-root (UID 10001). |
| `docker/entrypoint.sh` | Seeds proxy-mode settings idempotently, then serves bound to `127.0.0.1:8765`. |
| `k8s/base/` | Generic kustomize base: Deployment (app + reverse-proxy sidecar), Service, NetworkPolicy, sidecar ConfigMap. |
| `k8s/base/nginx.conf` | The same-Pod reverse-proxy sidecar config (the only Service target). |
| `k8s/overlays/homelab/` | Pins the base to node `2ndbrain`, the shared hostPath, the `Remote-User` identity, and the Traefik-only network restriction. |

## The security model in one breath

1. The **app binds `127.0.0.1:8765` only.** In proxy mode it *refuses* any other bind, because it trusts an identity
   header that anyone reaching the port directly could forge.
2. A **reverse-proxy sidecar** in the same Pod shares the network namespace, reaches the app over loopback, and is the
   **only container the Service targets.** It forwards — it does not authenticate.
3. Trust therefore rests entirely on the **edge** (Traefik + Authelia) and on **network isolation**:
   - the edge authenticates the user, **strips any inbound `Remote-*`**, and repopulates a trusted `Remote-User`;
   - only Traefik (`10.2.0.200`) can reach the Service (NetworkPolicy + host firewall, below).
4. The app checks the forwarded `Remote-User` against the allowlist (`jon`) and the Host against its public URL
   (`money.basalisk.net`). Approval cards still gate every write; this deployment broadens nothing.

```
 browser ──TLS──▶ Traefik (10.2.0.200)            K3s Pod (node 2ndbrain)
                   • Authelia forward-auth        ┌───────────────────────────────┐
                   • strip inbound Remote-*        │ nginx sidecar :8080  ──loop──▶ │ finance 127.0.0.1:8765
                   • set Remote-User: jon  ───────▶│  (only Service target)        │ (proxy mode, Remote-User)
                                                   └───────────────────────────────┘
```

## Build the image

```sh
# From the repository root (the build context):
docker build -f deploy/docker/Dockerfile -t hermes-finance:local .
# For K3s with containerd, import it onto node 2ndbrain:
docker save hermes-finance:local | ssh 2ndbrain 'sudo k3s ctr images import -'
```

The build uses `uv sync --frozen` (the lockfile is authoritative) and installs **no optional extras** — `mcp` and
`photos` are deliberately absent, so MCP returns 501 and photo generation degrades at the image layer.

## Apply the manifests

```sh
kustomize build deploy/k8s/overlays/homelab | kubectl apply -f -
# or: kubectl apply -k deploy/k8s/overlays/homelab
```

Before applying, set `runAsUser`/`runAsGroup` in `overlays/homelab/patch-deployment.yaml` to the UID/GID that owns
`/home/basalisk/finance-data` on the node (run `id -u basalisk` / `id -g basalisk`). This is what lets the host Hermes
plugin and the containerised app **share one `finance.db`** while keeping the `0700` directory / `0600` file
permissions. The overlay removes the base `fsGroup` so kubelet never recursively chowns that shared directory.

## Required Traefik + Authelia middleware (document only — do not edit live infrastructure)

The manifests here do **not** touch Traefik or Authelia. The following is the middleware your edge must already apply
to the `money.basalisk.net` router. Apply it yourself, out of band.

```yaml
# Authelia forward-auth: authenticate, then hand back the signed-in identity as Remote-User.
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata: { name: authelia, namespace: traefik }
spec:
  forwardAuth:
    address: http://authelia.authelia.svc:9091/api/verify?rd=https://auth.basalisk.net
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
#   rule: Host(`money.basalisk.net`)
```

Without the strip middleware a client could send `Remote-User: jon` and be trusted. With it, inbound copies are cleared
and only Authelia's value survives.

## Network isolation: only Traefik 10.2.0.200 may reach the service

The `homelab` overlay's NetworkPolicy allows ingress to the sidecar (`:8080`) **only from `10.2.0.200/32`** and denies
everything else. The base denies all ingress.

**NodePort caveat / fail-closed.** The default Service is `ClusterIP`: an in-cluster Traefik reaches it and the
NetworkPolicy *fully governs* access — the fail-closed default. If your Traefik runs **off-cluster** (the homelab edge
at `10.2.0.200`) it must reach a NodePort, and a NodePort can **bypass pod-level NetworkPolicy** via kube-proxy DNAT.
Kubernetes alone therefore cannot *prove* that only `10.2.0.200` reaches it. If you enable
`overlays/homelab/service-nodeport.yaml`, you MUST also add a host-firewall rule on node `2ndbrain`:

```sh
# Allow only Traefik to the NodePort; drop everything else. (Example; adapt to your firewall.)
sudo iptables -I INPUT -p tcp --dport 30876 -s 10.2.0.200 -j ACCEPT
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
ssh 2ndbrain 'sudo tar -C /home/basalisk/finance-data -czf - .' > finance-data.tgz
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
