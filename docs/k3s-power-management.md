# K3s power management and health

These workflows operate an existing K3s cluster. They do not install K3s,
change GitOps resources, manage applications, or embed private inventory.

## Inventory contract

The private Semaphore `static-yaml` inventory defines `masters` with exactly
one operationally last host, `workers` with the earlier hosts, and their
`k3s_cluster` parent. Every member is a K3s server/control-plane and embedded
etcd member; the group names specify operation order, not Kubernetes roles.

Each host supplies connection data and a unique unicast `mac_address`.
`k3s_node_name` optionally maps the inventory alias to the Kubernetes Node name
and defaults to `inventory_hostname`. Names must be nonempty and unique.
`expected_system_hostname` is optional and is checked only when explicitly set.
`k3s_binary_path` defaults to `/usr/local/bin/k3s`; it must be a regular,
executable, nonsymlink file and may be overridden in private inventory.

The older connectivity audit continues to use `k3s_servers`. Its migration to
the lifecycle group contract is deliberately deferred to a separate change.

## Power On

Power On must run for the complete `k3s_cluster`; the Semaphore template must
not set a limit. The first play targets that group but delegates its `run_once`
validation, WoL helper, and TCP waits to the controller. Therefore it can reject
a partial resulting host set before sending packets without first connecting to
a powered-off node.

MAC-bearing commands use `no_log`. A separate safe result contains only the
inventory name, category, and return code. All sends and TCP waits are attempted
before their aggregate gate. Recovered hosts validate K3s/systemd and their own
local `/readyz?verbose`. The controller requires exact equality between expected
mapped Node names and live Kubernetes Nodes, waits for Ready, and uncordons only
expected Nodes that are currently unschedulable.

The readyz check means "Kubernetes API readiness with etcd backend check". It
confirms that each server's local API reports a positive etcd backend check. It
does not audit etcd membership or endpoint status and must not be described as
a complete etcd health audit.

## Health Check

Health first probes every inventory SSH endpoint locally. Automatic fact
gathering is disabled; reachable hosts run an explicit `setup`, while every
unreachable host remains represented as `UNREACHABLE`. Final aggregation runs
on localhost, so an unavailable operational master still produces a critical
report.

Host output contains only total/available RAM and used percentage plus the same
values for root and explicitly configured `k3s_health_additional_mounts`. That
variable is a map from a safe public identifier to a private inventory path,
for example an identifier such as `data`; only the identifier is reported. It does
not expose block-device names or raw `df`/`free` output. Warning and critical
thresholds default to 80 and 90 percent and are configurable. Missing or zero
denominators are critical rather than divided by zero.

The shared local Python classifier processes only supplied Pod and Job JSON.
Only a Failed Pod whose matching Job is terminal is historical. Current
container and init-container waiting errors are critical. Pending is a warning
during `k3s_pod_pending_grace_seconds` and critical afterward. A terminating
pod is a warning. A historical Failed pod owned by a Job is a warning; active
waiting errors remain critical. Succeeded/Completed is healthy.

`k3s_health_expect_flux`, `k3s_health_expect_longhorn`, and
`k3s_health_expect_cnpg` default to `true`. An expected query failure, missing
CRD, malformed JSON, empty required resource kind, or missing required status is
critical. An explicitly disabled integration is not queried and reports
`NOT_CHECKED`, never `HEALTHY`. Longhorn evaluation also checks every Engine
`status.restoreStatus` entry for an active or failed restore.

Critical findings always fail Health. Warnings fail only with
`k3s_health_mode=strict`; `report` is the default. Info never changes the result.
No Secret or ConfigMap is read and no repair, restart, or systemd reset occurs.

## Approved full-cluster shutdown

Shutdown requires the complete un-limited `k3s_cluster`,
`k3s_power_action=shutdown`, and
`k3s_shutdown_confirm=SHUTDOWN_K3S_CLUSTER`. Its fail-closed read-only preflight
requires all hosts, exact Ready Node membership, safe Pods, ready nonempty Flux
resource kinds, available Longhorn BackupTargets, healthy volumes without a
restore requirement, no active/unknown backup, and nonempty ready CloudNativePG
clusters with all instances and a current primary.

The workflow intentionally does not drain. After preflight it reads initial
schedulability, cordons only previously schedulable Nodes, and records rollback
scope only from successful cordon results. A failed cordon, verification, or
final readyz check uncordons only those Nodes and stops before poweroff.

The final readyz check is the irreversible boundary. Below it there are no
Kubernetes, etcd, Flux, Longhorn, CNPG, or uncordon operations. Poweroff is
requested sequentially for `workers` and then the `masters` host. Local TCP
probes can report `POWER_OFF_REQUESTED`, `SSH_NOT_RESPONDING`,
`SSH_RESPONDING`, `POWER_OFF_NOT_CONFIRMED`, `POWER_OFF_REQUEST_FAILED`, or
`NOT_ATTEMPTED`. A closed SSH port never proves physical power state.

After a partial failure, only local TCP probes run; no rollback is attempted.
A successful run means that poweroff was requested for every host and SSH
stopped responding, not that physical power-off was independently confirmed.

## Semaphore templates

Create three manually reviewed templates:

| Template | Playbook | Controls |
|---|---|---|
| `K3S | 10 Power On` | `cluster/playbooks/power/k3s-power-on.yml` | No limit; private MAC/broadcast inputs |
| `K3S | 20 Health Check` | `cluster/playbooks/audit/k3s-health.yml` | Optional `k3s_health_mode=strict` |
| `K3S | 30 Approved Shutdown` | `cluster/playbooks/power/k3s-power-off.yml` | No limit; both confirmations |

Notifications default off. When enabled, URL and token come only from
`K3S_NTFY_URL` and `K3S_NTFY_TOKEN`, run locally without privilege, use
`no_log`, do not register responses, and cannot change the operation result.
Never schedule full shutdown automatically.
