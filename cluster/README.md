# K3s cluster playbooks

This directory contains reusable automation scoped to a K3s cluster. It does
not describe a particular cluster or include inventory.

## Required inventory groups

- `k3s_servers`: every K3s server/control-plane node that should be included in
  cluster audits.

The private Semaphore `static-yaml` inventory defines hosts, addresses,
connection users, ports, and any topology-specific variables. Those values must
not be committed to this repository.

## Playbooks

- `playbooks/audit/connectivity.yml`: verifies that Ansible can connect to the
  selected `k3s_servers` members and reports the resolved connection endpoint.

The playbook is non-mutating and does not use privilege escalation. Installation,
network, firewall, storage, upgrades, restart, and lifecycle operations are not
part of this public repository.
