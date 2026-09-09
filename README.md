# Homelab Ansible

Public Ansible playbooks for K3s lifecycle and Debian host maintenance. Real
inventory, addresses, credentials and environment-specific policy stay in
private Semaphore inventory and Key Store.

## Layout

- [`cluster/`](cluster/README.md): K3s health, UFW, Wake-on-LAN and shutdown;
- [`homelab/`](homelab/README.md): host audits, APT updates and approved reboot;
- [`scripts/`](scripts/): host onboarding and public-tree validation;
- [`docs/`](docs/): operational requirements.

## Safety rules

- `ansible.cfg` has no default inventory; select the private inventory explicitly.
- SSH host-key checking is enabled.
- APT playbooks target only `update_standard` or `update_automatic` and run
  serially. Automatic maintenance does not reboot.
- Reboot requires one host from `reboot_approved`, a matching target variable,
  a reboot marker and no failed systemd units.
- K3s power playbooks require the complete cluster without `--limit`.
- K3s shutdown requires two confirmations and checks Node and Longhorn state.
- UFW rules use private inventory values and preserve unrelated rules and
  default policies.

## Validation

```sh
ansible-galaxy collection install -r collections/requirements.yml
bash -n scripts/*.sh tests/*.sh
python3 tests/test-cluster-scripts.py
python3 tests/test-k3s-lifecycle.py
python3 tests/test-k3s-ufw.py
scripts/scan-public-tree.py scripts tests cluster docs .github README.md homelab/README.md
git diff --check
```

Use a temporary inventory with fictional hosts for `ansible-playbook
--syntax-check`. Do not commit inventory files.
