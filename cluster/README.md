# K3s cluster playbooks

This directory contains reusable audits and guarded power workflows for an
existing K3s cluster. It does not include inventory or cluster installation.

## Required private inventory groups

- `k3s_servers`: legacy connectivity-audit group, retained unchanged for now;
- `masters`: exactly one operationally last node for the new lifecycle workflows;
- `workers`: exactly two nodes processed before `masters` for shutdown;
- `k3s_cluster`: a children group containing exactly `masters` and `workers`.

Every member of `k3s_cluster` is treated as a K3s server/control-plane member
with embedded etcd. Group names define operational order only; `workers` does
not imply K3s agent-only nodes.

Semaphore private `static-yaml` inventory supplies connection settings and a
unique unicast `mac_address` for Power On. Inventory aliases match Kubernetes
Node names. No concrete private values belong in this repository.

## Playbooks

- `playbooks/audit/connectivity.yml`: existing `k3s_servers` connection audit;
  migration of this playbook is intentionally deferred.
- `playbooks/audit/k3s-health.yml`: basic read-only host and Node health in
  `report` or `strict` mode; extended audits are separate future playbooks.
- `playbooks/power/k3s-power-on.yml`: validates inventory, sends WoL to every
  node, then verifies host, API, etcd, and exact Ready Node state without
  changing scheduling.
- `playbooks/power/k3s-power-off.yml`: requires full-cluster scope and two
  confirmations, checks exact Ready Nodes plus active Longhorn backup/restore
  safety, then requests poweroff for `workers` sequentially and `masters` last.

The helper `scripts/send-wol.py` uses only Python's standard library and never
discovers addresses. See [`docs/k3s-power-management.md`](../docs/k3s-power-management.md)
for safety controls, variables, and Semaphore templates.
