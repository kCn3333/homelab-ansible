# K3s UFW

Playbook: `cluster/playbooks/power/k3s-ufw.yml`.

## Private inventory variables

- `k3s_management_cidr`: source for SSH on `22/tcp`;
- `k3s_haproxy_ip`: source for `6443/tcp`, `80/tcp`, `443/tcp`;
- `k3s_additional_ingress_ips`: list of additional sources for `6443/tcp`,
  `80/tcp`, `443/tcp`; use `[]` when empty;
- `k3s_cluster_cidr`: source for cluster TCP and UDP rules;
- `k3s_pod_cidr`: source allowed without a port restriction.

## Rules

- management: `22/tcp allow`;
- HAProxy and additional ingress: `6443/tcp`, `80/tcp`, `443/tcp`;
- cluster TCP: `6443`, `10250`, `2379`, `2380`, `9100`, `4443`, `4244`, `4240`;
- cluster UDP: `8472`, `51820`, `123`;
- Pod CIDR: allow all;
- ident: `113/tcp reject log`;
- other SSH sources: `22/tcp limit`.

The playbook preserves unrelated rules and default policies. It does not delete,
reset or manually reload UFW.

## Run

Check mode:

```bash
ansible-playbook \
  -i inventory.yml \
  cluster/playbooks/power/k3s-ufw.yml \
  --check --diff
```

Apply:

```bash
ansible-playbook -i inventory.yml cluster/playbooks/power/k3s-ufw.yml
```

Run the apply command again. An unchanged system must report:

```text
changed=0
failed=0
```
