# K3s UFW

Playbook: `cluster/playbooks/power/k3s-ufw.yml`.

## Inventory

Required private Semaphore inventory:

- nonempty `k3s_cluster` group;
- `ansible_host` for every member;
- inventory aliases matching Kubernetes Node names;
- `k3s_management_cidr`: source network for SSH on TCP port 22;
- `k3s_haproxy_ip`: HAProxy source address;
- `k3s_pod_cidr`: Pod source network;
- `k3s_service_cidr`: Service source network.

All addresses and obsolete rule definitions stay in private inventory. Do not
use `--limit`. The Semaphore SSH connection must originate in
`k3s_management_cidr` and use port 22.

Required on each node: UFW, sudo, `/etc/rancher/k3s/k3s.yaml`, Python Kubernetes
client and PyYAML in the Ansible interpreter.

## Install collections

```sh
ansible-galaxy collection install -r collections/requirements.yml
```

## Rules and order

1. Validate the complete inventory and all required variables.
2. Allow management SSH.
3. Allow HAProxy TCP ports 80, 443 and 6443; allow TCP ports 6443, 2379, 2380,
   10250, 4240 and 4244 plus UDP port 8472 from each node's `ansible_host`.
   Allow all Pod and Service source traffic; reject TCP port 113 with logging.
4. Delete rules explicitly listed in optional `k3s_ufw_obsolete_rules` (default `[]`).
5. Enable UFW and assert active status from the module result.
6. Require a running K3s service and Ready Nodes through each local kubeconfig.

`k3s_ufw_obsolete_rules` entries require `rule` (`allow`, `deny`, `reject` or
`limit`) and `from_ip`. Optional keys: `to_ip`, `port`, `proto`. Unknown keys and
required sources are rejected.

The playbook uses `community.general.ufw`. It preserves default policies and
unrelated rules. It does not reset UFW, use rule numbers, or perform a separate
reload. Check mode skips live post-configuration assertions.

UFW protects host services. Service and NodePort traffic can traverse NAT/FORWARD.
Router and inter-VLAN rules are outside this playbook.

## Validate

```sh
python3 tests/test-k3s-ufw.py
```
