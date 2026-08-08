# Semaphore configuration model

Semaphore configuration is intentionally not stored in this repository. The
target design uses two independent projects backed by the same public Git
repository:

| Project | Private inventory | Repository credential | Playbook scope |
|---|---|---|---|
| K3s Cluster | Separate `static-yaml` inventory | None | `cluster/playbooks/` |
| Homelab Maintenance | Separate `static-yaml` inventory | None | `homelab/playbooks/` |

## Repository

Because the repository is public, configure its Semaphore repository credential
as `None`. Do not attach a personal SSH key or host automation credential to
repository checkout.

## Inventory

Create and maintain real inventories directly in Semaphore using the
`static-yaml` inventory type. Do not synchronize them back into Git. The K3s
inventory must provide `k3s_servers`; the Homelab Maintenance inventory must
provide `homelab_managed` and may use the other abstract groups documented in
`homelab/README.md`.

Inventory holds non-secret connection metadata and group membership. Avoid
copying inventory content into task descriptions, logs, documentation, or pull
requests.

## Credentials and secrets

Store automation credentials and secrets exclusively in Semaphore Key Store.
Use separate, least-privilege host credentials for the two projects. Do not put
private keys, passwords, tokens, kubeconfigs, vault passwords, or secret extra
variables in this repository or in static inventory plaintext.

Bind credentials only to templates that require them. Start with the read-only
audit playbooks and validate routing, host-key trust, and access from the
Semaphore execution environment before designing mutating templates.
