# Public repository architecture

## Separation boundary

`cluster/` contains reusable playbooks that target the abstract
`k3s_servers` group. `homelab/` contains reusable playbooks for the separate
non-cluster inventory and target the abstract `homelab_managed` group. A
Semaphore task template must always select one of these inventory scopes
explicitly.

The repository intentionally contains no concrete host list, addressing plan,
topology, environment domain, connection account, SSH port mapping, firewall
policy, route, VLAN, or secret. Real inventory is private `static-yaml` managed
in Semaphore.

## Safety model

- There is no default inventory in `ansible.cfg`.
- SSH host-key verification remains enabled.
- Remote users and privilege escalation are not globally configured.
- Included playbooks are read-only audits.
- Credentials and sensitive variables are not stored in Git.
- Mutating automation requires separate design, narrow groups, explicit
  controls, and validation against the private environment.

## Public/private boundary

The public repository owns playbook logic, abstract group contracts, and
general operational documentation. Semaphore owns concrete inventory, host and
group variables, credentials, and task-template bindings. This boundary avoids
publishing a topology that could be reconstructed from otherwise harmless
individual settings.
