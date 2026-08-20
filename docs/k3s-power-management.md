# K3s lifecycle operations

The three existing playbook paths remain stable for Semaphore, but each now has
one narrow purpose: wake and verify the cluster, report basic health, or perform
an explicitly approved shutdown. Real inventory and credentials remain in
Semaphore and are never stored in this repository.

## Inventory contract

The private `static-yaml` inventory must define exactly:

- one host in `masters`;
- two hosts in `workers`;
- all three, and no other hosts, in the `k3s_cluster` parent;
- no host shared between `masters` and `workers`;
- `ansible_host` for every host;
- a unique unicast `mac_address` for every host used by Power On.

Inventory aliases must match Kubernetes Node names. Every member is a K3s
server/control-plane/embedded-etcd node; `masters` and `workers` describe only
operational order. Connection users, ports, inventory, MAC addresses, and
credentials remain private Semaphore data.

## Power On

`cluster/playbooks/power/k3s-power-on.yml` rejects any `--limit`. Its localhost
play validates the complete inventory and all MAC values without connecting to
cluster hosts. It calls `cluster/scripts/send-wol.py` for every host, suppresses
MAC-bearing output, and only after all three attempts waits for every SSH port.
A failed send or a host that does not return fails the aggregate gate.

Recovered hosts must pass Ansible connectivity, noninteractive sudo, active
K3s, and local API readiness including `[+]etcd ok`. The master then confirms
the exact Node set and that every Node is Ready. Power On does not cordon,
uncordon, drain, repair services, or inspect workloads and platform integrations.

Required private input is `k3s_wol_broadcast`. Optional controls are
`k3s_wol_port`, `k3s_wol_count`, `k3s_wol_interval`, and
`k3s_ssh_wait_timeout`.

## Approved Shutdown

`cluster/playbooks/power/k3s-power-off.yml` rejects `--limit` and requires:

```text
k3s_power_action=shutdown
k3s_shutdown_confirm=SHUTDOWN_K3S_CLUSTER
```

Before mutation it verifies connectivity, noninteractive sudo, active K3s,
local API and etcd readiness, exact Ready Node membership, and the Longhorn
storage-operation gate. The storage gate checks only for active backups,
active restores, and restore errors. It deliberately does not block on
BackupTarget availability. Set `k3s_shutdown_check_longhorn=false` only after
explicit review; the result is then reported as `NOT_CHECKED`.

There is no cordon, drain, rollback, restart, or automatic repair. Workers are
processed sequentially in inventory order, followed by the master. Each host
receives `systemctl poweroff --no-block`, then the controller waits for its SSH
port to stop responding before continuing.

The comment marking the safety boundary is also an architectural rule: after
the first poweroff can be requested, no Kubernetes API query is allowed. Only
the next poweroff request and local TCP observation may follow. A successful
probe is reported as `SSH_NOT_RESPONDING`; that observation does not prove
physical power-off. If a step fails after shutdown begins, the report identifies
the stage and host, then separates SSH-responsive hosts from hosts that are not
responding or whose reachability is unknown; it stops before the next host.

## Basic Health

`cluster/playbooks/audit/k3s-health.yml` is read-only. It reports SSH reachability,
K3s service state and version, local API/etcd readiness, failed systemd unit
count, reboot marker, basic RAM usage, and root filesystem usage. From the
master it reports API availability, missing and unexpected Nodes, and each
Node's Ready state.

`k3s_health_mode=report` emits findings without failing solely because of
failed units, a reboot marker, or resource-usage warnings.
`k3s_health_mode=strict` fails for warnings or critical findings. Fundamental
errors such as an incomplete inventory or inability to read and parse cluster
state can fail either mode because a trustworthy report cannot be produced.

Flux, Longhorn, CNPG, Pods, and Jobs are outside Basic Health. Future dedicated
audits are planned at these paths:

- `cluster/playbooks/audit/k3s-health-gitops.yml`;
- `cluster/playbooks/audit/k3s-health-storage.yml`;
- `cluster/playbooks/audit/k3s-health-workloads.yml`.

## Semaphore templates

Keep the existing templates and paths:

| Template | Playbook |
|---|---|
| `K3S | 10 Power On` | `cluster/playbooks/power/k3s-power-on.yml` |
| `K3S | 20 Health Check` | `cluster/playbooks/audit/k3s-health.yml` |
| `K3S | 30 Approved Shutdown` | `cluster/playbooks/power/k3s-power-off.yml` |

Neither power template may define a limit. Shutdown must never be scheduled
automatically.
