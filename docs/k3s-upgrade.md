# Controlled K3s upgrade

`cluster/playbooks/maintenance/k3s-upgrade.yml` upgrades only the K3s binary on
the existing three-server HA cluster. It does not modify systemd units, K3s
configuration, join tokens, networking, or other cluster components.

## Requirements and execution

The private inventory must contain exactly one `masters` host, two `workers`
hosts, and their exact union as `k3s_cluster`. These are operational ordering
names only: all three hosts run as K3s servers, control-plane members, and
embedded-etcd members. Every inventory alias must match its Kubernetes Node
name. All servers must start healthy, report `x86_64`, and run the same full K3s
version. Do not use `--limit`.

Provide one explicit release version; channel names are not accepted:

```bash
ansible-playbook \
  cluster/playbooks/maintenance/k3s-upgrade.yml \
  --extra-vars 'k3s_target_version=v1.34.10+k3s1'
```

The playbook rejects downgrades and jumps over more than one Kubernetes minor
version. If every server already has the target version, it exits without
changing the cluster, but still performs all final version, Node membership,
Node readiness, and kubelet proxy checks.

A mixed starting version state is deliberately rejected, including after an
interrupted upgrade. Diagnose the stopped node and cluster health without
rerunning the upgrade or replacing more binaries. Review the retained binary,
service logs, local API and etcd readiness, Kubernetes Node readiness, and the
completed-node state. An operator must then make an explicit manual decision
whether to continue toward the target version or restore a previous binary;
the playbook does not choose or perform rollback automatically.

## Order and safety gates

Before replacing a binary, the playbook saves one embedded-etcd snapshot on the
`masters` host named `pre-k3s-upgrade-<safe-target-version>`. It then upgrades
the two `workers` one at a time in inventory order and upgrades `masters` last.
The previous binary is retained by Ansible's file backup mechanism; database or
binary rollback is not automatic. After each replacement, the task output
prints `Previous K3s binary backup:` followed by the remote `backup_file` path
created beside the installed binary. Record that path before manual recovery.

Each downloaded amd64 binary is checked against the official SHA-256 release
list before it atomically replaces `/usr/local/bin/k3s`. Only `k3s.service` is
restarted. The next server is not touched until the current service, local API,
embedded etcd, installed version, and Kubernetes Node readiness are confirmed.

Success requires the target version on all three servers, the exact three-node
Kubernetes membership with every Node Ready, and an `ok` kubelet health proxy
response for every API-server-to-Node pair (the full 3 x 3 matrix). A tunnel
failure stops the playbook and remains visible; the playbook performs no tunnel
resynchronization.

After a successful upgrade, perform a separate controlled cold start later to
validate the full cluster startup path independently.
