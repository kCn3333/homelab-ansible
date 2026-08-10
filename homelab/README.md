# Homelab maintenance playbooks

This directory contains reusable audits for systems outside the K3s cluster.
It does not include real inventory or environment-specific host policy.

## Inventory groups

The private Semaphore `static-yaml` inventory may use these abstract groups:

- `homelab_managed`: required parent group containing every host selected for
  the general homelab audit playbooks;
- `update_standard`: explicit safety gate for systems eligible for the shared
  APT maintenance policy;
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

The audit playbooks target `homelab_managed` within the explicitly selected
Homelab Maintenance inventory. The APT playbooks target only
`update_standard`; a host must be assigned to that group explicitly. Because
the repository has no default inventory, the playbooks cannot implicitly cross
into the separate cluster project.

## Playbooks

- `playbooks/audit/connectivity.yml`: checks Ansible connectivity.
- `playbooks/audit/preflight.yml`: reports operating system, kernel, root disk
  availability, memory, reboot-required state, available APT updates on Debian
  family systems, and Docker/Python-independent system facts without changing
  the host.
- `playbooks/maintenance/apt-preview.yml`: reads held packages and reboot state,
  simulates `apt-get --no-remove upgrade`, and fails if the simulation fails or
  proposes a removal. It does not refresh the APT cache or change the host.
- `playbooks/maintenance/apt-upgrade.yml`: refreshes the APT cache, repeats the
  no-removal simulation as a safety gate, and installs a safe upgrade while
  preserving held packages and existing configuration files.

Run `apt-preview.yml` first and review its result before running
`apt-upgrade.yml`. Both playbooks process one `update_standard` host at a time.
Autoremove and automatic reboot are disabled. Package installation scripts may
nevertheless restart individual services associated with updated packages; the
playbook does not add any host or service restart handlers.

Real inventory, host data, connection settings, and credentials remain only in
Semaphore. Do not add an inventory file to this repository. Reboots,
shutdowns, security policy, SSH changes, and cleanup are not included.
