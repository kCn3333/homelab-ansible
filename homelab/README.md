# Homelab maintenance playbooks

This directory contains reusable audits for systems outside the K3s cluster.
It does not include real inventory or environment-specific host policy.

## Inventory groups

The private Semaphore `static-yaml` inventory may use these abstract groups:

- `homelab_managed`: required parent group containing every host selected for
  the general homelab audit playbooks;
- `update_standard`: systems eligible for a shared maintenance policy;
- `controllers`: appliance-like service controllers;
- `edge`: DNS, proxy, load-balancer, or public-facing systems;
- `infrastructure`: supporting compute, backup, and monitoring systems;
- `lab`: experimental or special-purpose systems;
- `docker_hosts`: systems intended for Docker-specific automation;
- `ssh_bootstrap_required`: systems whose current SSH access is not ready or
  not yet verified.

Groups may overlap. Membership never grants permission to mutate a system by
itself. Host addresses, aliases, users, ports, credentials, and privilege
settings remain private in Semaphore.

The current audit playbooks target `homelab_managed` within the explicitly
selected Homelab Maintenance inventory. Because the repository has no default
inventory, they cannot implicitly cross into the separate cluster project.

## Playbooks

- `playbooks/audit/connectivity.yml`: checks Ansible connectivity.
- `playbooks/audit/preflight.yml`: reports operating system, kernel, root disk
  availability, memory, reboot-required state, available APT updates on Debian
  family systems, and Docker/Python-independent system facts without changing
  the host.

Updates, reboots, shutdowns, security policy, SSH changes, and cleanup are not
included.
