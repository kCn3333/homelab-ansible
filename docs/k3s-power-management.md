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
- exactly one host in `k3s_wol_gateway`, with no overlap with `k3s_cluster`;
- `ansible_host` for every host;
- a unique unicast `mac_address` for every host used by Power On.

Inventory aliases must match Kubernetes Node names. Every member is a K3s
server/control-plane/embedded-etcd node; `masters` and `workers` describe only
operational order. Connection users, ports, inventory, MAC addresses, and
credentials remain private Semaphore data.

## Power On

`cluster/playbooks/power/k3s-power-on.yml` rejects any `--limit`. Its localhost
play validates the complete inventory and all MAC values without connecting to
cluster hosts. The separate `k3s_wol_gateway` is reached by SSH through its
normal management connection; Power On does not assume the Semaphore execution
environment owns host network interfaces.

Before activation, the gateway play validates its inputs, requires active
`systemd-networkd`, confirms that the existing profile/interface is known to
`networkctl`, and runs `networkctl up`. It then requires an active interface
with a global IPv4 address and uses `/usr/bin/python3` to run
`cluster/scripts/send-wol.py` for every node.

The activation lifecycle is enclosed in `block`/`always`. Cleanup always runs
`networkctl down`, including when activation, address validation, or a send
fails. Only after successful deactivation do SSH waits begin.

After SSH recovery, every host must pass Ansible connectivity and noninteractive
sudo. Power On then waits first for the K3s service to reach `active`, and next
for the local API readiness response including `[+]etcd ok`. Each stage uses a
bounded retry window and continues immediately when its readiness conditions are
met. The master then confirms the exact Node set and that every Node is Ready.
Power On does not start or restart services, cordon, uncordon, drain, repair the
cluster, or inspect workloads and platform integrations.

Required private gateway inputs are `k3s_wol_interface` and
`k3s_wol_broadcast`. Power On uses fixed public safety values: a 120-second
activation timeout, WOL port 9, three packets, and a 0.2-second interval.
`k3s_ssh_wait_timeout` controls the subsequent SSH wait. Service and local API/etcd recovery waits use
`k3s_recovery_retries` (default `150`) and `k3s_recovery_delay` (default `2`
seconds); together their defaults allow about 300 seconds for each stage.

### Gateway network prerequisite

The gateway must already have a Netplan VLAN profile rendered by
`systemd-networkd`, attached to the correct parent, with `activation-mode: manual`,
`optional: true`, and DHCP or static addressing managed outside these
lifecycle playbooks. The profile must not activate automatically at boot.
Lifecycle automation never edits Netplan. Public examples use placeholders such
as `<wol-interface>`, `<wol-broadcast-address>`, and `<node-mac>`.

## Approved Shutdown

`cluster/playbooks/power/k3s-power-off.yml` rejects `--limit` and requires:

```text
k3s_power_action=shutdown
k3s_shutdown_confirm=SHUTDOWN_K3S_CLUSTER
```

Power Off uses normal IP routing and does not require or contact the WOL gateway.

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
