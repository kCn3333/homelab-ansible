#!/usr/bin/env bash
set -Eeuo pipefail

show_packages=false
usage() { echo "Usage: $0 [--show-packages]"; }
while (($#)); do
  case "$1" in
    --show-packages) show_packages=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Error: unknown argument" >&2; exit 2 ;;
  esac
done

os_id=unknown
version_id=unknown
if [[ -r /etc/os-release ]]; then
  while IFS='=' read -r key value; do
    value=${value%\"}; value=${value#\"}
    case "$key" in ID) os_id=$value ;; VERSION_ID) version_id=$value ;; esac
  done < /etc/os-release
fi

present() { command -v "$1" >/dev/null 2>&1 && echo present || echo missing; }
account_exists=false
password_locked=unknown
authorized_keys_exists=false
if getent passwd ansible >/dev/null 2>&1; then
  account_exists=true
  if command -v passwd >/dev/null 2>&1; then
    status=$(passwd -S ansible 2>/dev/null | awk '{print $2}' || true)
    [[ "$status" == L || "$status" == LK ]] && password_locked=true || password_locked=false
  fi
  [[ -f /home/ansible/.ssh/authorized_keys ]] && authorized_keys_exists=true
fi

path_status() {
  local path=$1
  if [[ -e "$path" ]]; then stat -c '%U:%G %a' "$path" 2>/dev/null || echo unreadable; else echo absent; fi
}

service_state() {
  local unit=$1
  if command -v systemctl >/dev/null 2>&1; then systemctl is-active "$unit" 2>/dev/null || true; else echo unavailable; fi
}

sudo_noninteractive=false
if [[ "$account_exists" == true ]] && command -v sudo >/dev/null 2>&1; then
  sudo -n -u ansible sudo -n true >/dev/null 2>&1 && sudo_noninteractive=true
fi
sudoers_valid=unavailable
if command -v visudo >/dev/null 2>&1; then
  visudo -cf /etc/sudoers >/dev/null 2>&1 && sudoers_valid=true || sudoers_valid=false
fi

failed_units=unknown
if command -v systemctl >/dev/null 2>&1; then
  failed_units=$(systemctl --failed --no-legend --plain --no-pager 2>/dev/null | awk 'NF {count++} END {print count+0}' || true)
  [[ -n "$failed_units" ]] || failed_units=unknown
fi

apt_rc=127
apt_inst=0
apt_remv=0
apt_packages=()
if command -v apt-get >/dev/null 2>&1; then
  apt_output=$(mktemp)
  trap 'rm -f -- "$apt_output"' EXIT
  set +e
  apt-get --simulate --no-remove upgrade >| "$apt_output" 2>&1
  apt_rc=$?
  set -e
  apt_inst=$(awk '/^Inst / {count++} END {print count+0}' "$apt_output")
  apt_remv=$(awk '/^Remv / {count++} END {print count+0}' "$apt_output")
  if [[ "$show_packages" == true ]]; then
    mapfile -t apt_packages < <(awk '/^(Inst|Remv) / {print $2}' "$apt_output" | sort -u)
  fi
fi

reboot_required=false
[[ -e /var/run/reboot-required ]] && reboot_required=true
printf 'os_id=%s\nversion_id=%s\narchitecture=%s\n' "$os_id" "$version_id" "$(uname -m)"
printf 'python3=%s\nsudo=%s\nopenssh_server=%s\n' "$(present python3)" "$(present sudo)" "$(command -v sshd >/dev/null 2>&1 && echo present || echo missing)"
printf 'ssh_service=%s\nssh_socket=%s\n' "$(service_state ssh.service)" "$(service_state ssh.socket)"
printf 'ansible_account=%s\nansible_password_locked=%s\nauthorized_keys=%s\n' "$account_exists" "$password_locked" "$authorized_keys_exists"
printf 'home_ansible=%s\nssh_directory=%s\nauthorized_keys_metadata=%s\nsudoers_metadata=%s\n' "$(path_status /home/ansible)" "$(path_status /home/ansible/.ssh)" "$(path_status /home/ansible/.ssh/authorized_keys)" "$(path_status /etc/sudoers.d/90-semaphore-ansible)"
printf 'sudo_noninteractive=%s\nsudoers_valid=%s\nfailed_units=%s\n' "$sudo_noninteractive" "$sudoers_valid" "$failed_units"
printf 'apt_simulation_rc=%s\napt_planned_installations_or_updates=%s\napt_planned_removals=%s\nreboot_required=%s\n' "$apt_rc" "$apt_inst" "$apt_remv" "$reboot_required"
if [[ "$show_packages" == true ]]; then printf 'apt_planned_packages=%s\n' "${apt_packages[*]:-}"; fi
exit "$([[ $apt_rc -eq 0 ]] && echo 0 || echo 1)"
