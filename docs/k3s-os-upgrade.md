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

Preflight checks Ubuntu 24.04, architecture, K3s service and local API/etcd,
matching K3s versions, package state, filesystem space, exact Ready and
schedulable Node membership, a 9/9 API-to-kubelet matrix, and healthy Longhorn
volumes. It then refreshes the APT cache on all three nodes, previews a dist
upgrade in check mode, and reads each reboot marker before any cordon. Failures
stop the run without automatic repair.

If no node needs packages or a reboot, the playbook skips the etcd snapshot and
all per-node maintenance. Otherwise it saves one named snapshot on `master`.
Only nodes needing work are cordoned, drained, upgraded, rebooted when required,
checked for recovery and uncordoned. A 9/9 API-to-kubelet check follows each
updated node. The final cluster checks still cover every node, including Cilium,
Longhorn, and Pod phases limited to `Running` or `Succeeded`. The final table
marks nodes without work `CURRENT` and their per-node actions `SKIPPED`.

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
