# Homelab maintenance playbooks

This directory contains reusable audits for systems outside the K3s cluster.
It does not include real inventory or environment-specific host policy.

## Inventory groups

The private Semaphore `static-yaml` inventory may use these abstract groups:

- `homelab_managed`: required parent group containing every host selected for
  the general homelab audit playbooks;
- `update_standard`: explicit safety gate for systems eligible for the shared
  APT maintenance policy;
- `update_automatic`: explicit safety gate for systems eligible for automatic
  distribution upgrades and cleanup;
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
- `playbooks/audit/notification-test.yml`: sends a local test notification
  using secret environment variables without exposing their values.
- `playbooks/maintenance/apt-preview.yml`: reads held packages and reboot state,
  simulates `apt-get --no-remove upgrade`, and fails if the simulation fails or
  proposes a removal. It does not refresh the APT cache or change the host.
- `playbooks/maintenance/apt-upgrade.yml`: refreshes the APT cache, repeats the
  no-removal simulation as a safety gate, and installs a safe upgrade while
  preserving held packages and existing configuration files.
- `playbooks/maintenance/apt-automatic.yml`: processes one explicitly selected
  `update_automatic` host at a time, performs an APT distribution upgrade,
  removes obsolete packages with purge, runs autoclean and clean, and fails if
  any systemd units remain failed. After reloading systemd, it clears only
  orphaned failed entries whose unit files are no longer present, then checks
  all failed units again.

Run `apt-preview.yml` first and review its result before running
`apt-upgrade.yml`. Both playbooks process one `update_standard` host at a time.
Autoremove and automatic reboot are disabled. Package installation scripts may
nevertheless restart individual services associated with updated packages; the
playbook does not add any host or service restart handlers.

Automatic maintenance also never reboots a host. When a reboot is required or
maintenance fails, it sends a notification through the URL and bearer token in
`MAINTENANCE_NTFY_URL` and `MAINTENANCE_NTFY_TOKEN`. These values must be
provided only as secret environment variables in Semaphore and must never be
stored in this repository. A completely successful run that does not require a
reboot sends no notification. Package installation scripts may still restart
their own services during a distribution upgrade. A host failure triggers that
host's notification and failure result without preventing the next
`update_automatic` host from being processed.

Real inventory, host data, connection settings, and credentials remain only in
Semaphore. Do not add an inventory file to this repository. Reboots,
shutdowns, security policy, SSH changes, and cleanup are not included.
