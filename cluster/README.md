# K3s cluster playbooks

This directory contains reusable audits and guarded power workflows for an
existing K3s cluster. It does not include inventory or cluster installation.

## Required private inventory groups

- `k3s_servers`: legacy connectivity-audit group, retained unchanged for now;
- `masters`: exactly one operationally last node for the new lifecycle workflows;
- `workers`: one or more nodes processed before `masters` for shutdown;
- `k3s_cluster`: a children group containing exactly `masters` and `workers`.

Every member of `k3s_cluster` is treated as a K3s server/control-plane member
with embedded etcd. Group names define operational order only; `workers` does
not imply K3s agent-only nodes.

Semaphore private `static-yaml` inventory supplies connection settings, a
unique `mac_address`, and optional `k3s_node_name` for every lifecycle node.
The Node name defaults to `inventory_hostname`. `k3s_binary_path` defaults to
`/usr/local/bin/k3s` and can be overridden per host. No concrete private values
belong in this repository.

## Playbooks

- `playbooks/audit/connectivity.yml`: existing `k3s_servers` connection audit;
  migration of this playbook is intentionally deferred.
- `playbooks/audit/k3s-health.yml`: read-only host and cluster health in
  `report` or `strict` mode.
- `playbooks/power/k3s-power-on.yml`: validates inventory, sends WoL to every
  node, recovers host and cluster readiness, and selectively uncordons recovered
  inventory nodes.
- `playbooks/power/k3s-power-off.yml`: requires full-cluster scope and two
  confirmations, runs a fail-closed workload preflight, cordons without drain,
  then requests poweroff for `workers` sequentially and `masters` last.

The helper `scripts/send-wol.py` uses only Python's standard library and never
discovers addresses. See [`docs/k3s-power-management.md`](../docs/k3s-power-management.md)
for safety controls, variables, and Semaphore templates.
