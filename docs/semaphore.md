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
inventory must retain `k3s_servers` for the legacy connectivity audit and
provide `masters`, `workers`, and their `k3s_cluster` parent for lifecycle work;
the Homelab Maintenance inventory must
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

## K3s power templates

The K3s project may expose three deliberately ordered templates:

- `K3S | 10 Power On`;
- `K3S | 20 Health Check`;
- `K3S | 30 Approved Shutdown`.

Power On requires private inventory MAC values plus reviewed WoL broadcast,
port, SSH timeout, optional Node-name mappings, and no Semaphore limit.
Approved Shutdown requires both
`k3s_power_action=shutdown` and
`k3s_shutdown_confirm=SHUTDOWN_K3S_CLUSTER`. It cordons but does not drain.
Template details and additional controls are in
[`k3s-power-management.md`](k3s-power-management.md). Do not schedule shutdown
automatically.

The lifecycle inventory may override `k3s_binary_path` (default
`/usr/local/bin/k3s`) and `k3s_node_name` (default `inventory_hostname`) per
host. `expected_system_hostname` is optional and is checked only when set.
`k3s_health_expect_flux`, `k3s_health_expect_longhorn`, and
`k3s_health_expect_cnpg` default to `true`: missing CRDs, malformed results,
empty required resources, or unhealthy state then fail closed. Setting an
integration explicitly to `false` skips its queries and reports `NOT_CHECKED`.

Neither Power On nor Approved Shutdown may use `--limit`. Shutdown does not
drain and requires both confirmation variables above. Its success means only
that every poweroff request was accepted and the corresponding SSH endpoint was
observed as `SSH_NOT_RESPONDING`; it does not confirm physical power state.

GitHub CI may download pinned dependencies and an isolated test image. That is
not an offline-test guarantee. It never connects to homelab infrastructure,
sends WoL or ntfy, or executes a playbook against hosts.
