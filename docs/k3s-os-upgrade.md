# Controlled Ubuntu package upgrade for K3s servers

Run `cluster/playbooks/maintenance/k3s-os-upgrade.yml` from a Semaphore task
using the existing private inventory. It already defines `masters`, `workers`,
`k3s_cluster`, and `k3s_servers`; no inventory change is needed. The playbook
checks their cluster scope and requires aliases matching Kubernetes Node names.
Do not use `--limit`.

Set `k3s_os_upgrade_confirm: true` explicitly for each run. Optionally set
`k3s_os_upgrade_order` to a list containing each alias once. The default is
`[worker2, master, worker1]`. `worker1` is last because it usually hosts
single-instance CloudNativePG databases and RWO volumes. Draining that node
may briefly interrupt those databases.

The read-only preflight checks Ubuntu 24.04, architecture, K3s service and
local API/etcd, matching K3s versions, package state, filesystem space, exact
Ready and schedulable Node membership, a 9/9 API-to-kubelet matrix, and healthy
Longhorn volumes. Failures stop the run without automatic repair. A single
named etcd snapshot is taken on `master` before any package change, then each
node is cordoned, drained through eviction, upgraded with APT, rebooted only if
required, checked for recovery, and uncordoned. Final checks include Cilium on
all nodes and require every Pod to be in phase `Running` or `Succeeded`.

If a node fails after cordon, the playbook stops before the next node and leaves
it cordoned. Inspect the failed Ansible task, Node description, assigned Pods,
events, `systemctl status k3s`, `journalctl -u k3s`, and local API/etcd state.
Resolve the underlying problem and verify local API, etcd, Node Ready, Cilium,
Longhorn and workload health before manually running `sudo k3s kubectl
uncordon <node>` on `master`. Check the Node is schedulable afterward. Do not
rerun the full upgrade blindly after a partial package update.

An `UNREACHABLE` result can prevent Ansible from entering `rescue` or collecting
local service logs. The serial queue still stops; treat the node as cordoned
until its scheduling state has been verified from a healthy server.
