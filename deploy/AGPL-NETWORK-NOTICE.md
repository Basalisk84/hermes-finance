# AGPL source-availability notice (network deployment)

hermes-finance is free software licensed under the **GNU Affero General Public License, version 3 or later
(AGPL-3.0-or-later)**. See [`LICENSE`](../LICENSE) for the full text.

## Why this notice exists

The artifacts in `deploy/` run hermes-finance as a network service (e.g. at `https://finance.example.invalid`). Section 13 of
the AGPL ("Remote Network Interaction") requires that **all users who interact with a modified version over a network
be offered the opportunity to receive its Corresponding Source.** This notice, and the obligations below, apply to
anyone who deploys this software — modified or not — so that users can obtain the source of the version they are
actually using.

## Your obligation when you deploy a modified version

If you run a **modified** version of hermes-finance that users reach over a network, you must offer those users the
Corresponding Source of **your** running version — not merely upstream. Concretely:

1. **Publish your source.** Make the exact source (including your local modifications and these deployment artifacts)
   available to every user who interacts with the service, at no charge, by a customary means of distributing software
   (e.g. a public Git repository).
2. **Tell users where to get it.** Offer a prominent, reachable link to that source from the running instance. The
   upstream project is published at `https://github.com/Basalisk84/hermes-finance`; replace this with the URL of your
   own fork if you modify the code.
3. **Keep the licence intact.** Ship this notice and `LICENSE` with any copy or deployment, and do not add further
   restrictions.

## This deployment

- **Upstream source:** `https://github.com/Basalisk84/hermes-finance`
- **Licence:** AGPL-3.0-or-later (`LICENSE`)
- **Modifications in this deployment:** the container image, Kubernetes manifests, and reverse-proxy configuration
  under `deploy/`. If you change the application itself, record your changes and point users at your fork.

Operators should surface a "Source" link to the above repository (or their fork) from the deployed instance so that
the AGPL §13 offer reaches every network user.
