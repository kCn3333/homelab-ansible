# Homelab Ansible playbooks

This public repository contains reusable Ansible playbooks and documentation.
It deliberately contains no real inventory, host addresses, account names,
credentials, topology, or environment-specific access policy.

Automation is separated into two scopes:

- [`cluster/`](cluster/README.md) for K3s server audits;
- [`homelab/`](homelab/README.md) for general system audits outside the cluster.

## Inventory model

Real inventory is maintained as `static-yaml` in Semaphore and is never stored
in this repository. Every command or task template must select the appropriate
Semaphore inventory explicitly because [`ansible.cfg`](ansible.cfg) defines no
default inventory.

The playbooks expect abstract groups documented in the component READMEs. Group
membership, connection variables, privilege escalation, and host-specific
settings belong to the private Semaphore inventory. Secrets belong exclusively
in Semaphore Key Store or in a separately designed Ansible Vault workflow.

## Included playbooks

The repository provides non-mutating audits and explicitly gated APT
maintenance:

- cluster connectivity;
- homelab connectivity;
- homelab system preflight information;
- a read-only preview of safe APT updates;
- serial installation of safe APT updates for hosts explicitly assigned to
  `update_standard`;
- serial automatic APT maintenance for hosts explicitly assigned to
  `update_automatic`;
- a local test for maintenance notifications.

Run the APT preview before the upgrade playbook. The `update_standard` group is
an explicit safety gate: update playbooks never target `all` or the broader
`homelab_managed` group. Updates run one host at a time. Automatic reboot and
package autoremove are disabled, although package installation scripts may
still restart the particular services managed by those packages.

Automatic maintenance is a separate, explicit policy for the
`update_automatic` group. It performs a distribution upgrade followed by
autoremove with purge, autoclean, and clean, but never reboots a host
automatically. Maintenance notifications use `MAINTENANCE_NTFY_URL` and
`MAINTENANCE_NTFY_TOKEN`, supplied only as secret environment variables in
Semaphore. The repository contains neither notification endpoints nor tokens.

No shutdown, firewall, SSH configuration, cluster installation, or cleanup
automation is included.

## Local development

Syntax validation requires a temporary inventory containing fictional hosts and
the documented groups. Do not add that inventory to Git. For example, reserve
documentation addresses from RFC 5737 such as `192.0.2.0/24` if a local test
needs an address.

SSH host-key checking remains enabled. The playbooks do not configure remote
users or privilege escalation.

## License

No license file is currently included. Confirm licensing terms before reuse or
redistribution.
