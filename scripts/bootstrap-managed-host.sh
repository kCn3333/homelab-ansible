#!/usr/bin/env bash
set -Eeuo pipefail

apply=false
install_missing=false
repair=false
allow_sudo=false
public_key=""
tmp_dir=""
account_created=false
home_created=false
rollback_needed=false
managed_paths_touched=false
authorized_keys_existed=false
sudoers_existed=false
authorized_keys_touched=false
sudoers_touched=false
key_pending=""
sudo_pending=""
home_meta=""
ssh_meta=""
ssh_dir_existed=false

usage() { echo "Usage: $0 --public-key PATH [--apply] [--install-missing] [--repair] [--allow-passwordless-sudo]"; }
die() { echo "Error: $1" >&2; exit "${2:-2}"; }
cleanup() {
  rc=$?
  if ((rc != 0)) && [[ "$rollback_needed" == true && "$managed_paths_touched" == true ]]; then
    if [[ "$authorized_keys_touched" == true ]]; then
      if [[ "$authorized_keys_existed" == true ]]; then
        cp -p -- "$tmp_dir/authorized_keys.before" /home/ansible/.ssh/authorized_keys
      else
        rm -f -- /home/ansible/.ssh/authorized_keys
      fi
    fi
    if [[ "$sudoers_touched" == true ]]; then
      if [[ "$sudoers_existed" == true ]]; then
        cp -p -- "$tmp_dir/sudoers.before" /etc/sudoers.d/90-semaphore-ansible
      else
        rm -f -- /etc/sudoers.d/90-semaphore-ansible
      fi
    fi
    if [[ -n "$ssh_meta" ]]; then
      IFS=: read -r ssh_uid ssh_gid ssh_mode <<< "$ssh_meta"
      chown "$ssh_uid:$ssh_gid" /home/ansible/.ssh
      chmod "$ssh_mode" /home/ansible/.ssh
    elif [[ "$ssh_dir_existed" != true ]]; then
      rmdir /home/ansible/.ssh 2>/dev/null || true
    fi
    if [[ -n "$home_meta" ]]; then
      IFS=: read -r home_uid home_gid home_mode <<< "$home_meta"
      chown "$home_uid:$home_gid" /home/ansible
      chmod "$home_mode" /home/ansible
    fi
    if [[ "$account_created" == true && "$home_created" == true ]]; then
      entry=$(getent passwd ansible || true)
      home=$(printf '%s' "$entry" | cut -d: -f6)
      if [[ "$home" == /home/ansible ]]; then
        userdel --remove ansible >/dev/null 2>&1 || true
      else
        echo "Rollback refused unexpected account path" >&2
      fi
    fi
  fi
  [[ -z "$key_pending" ]] || rm -f -- "$key_pending"
  [[ -z "$sudo_pending" ]] || rm -f -- "$sudo_pending"
  if [[ -n "$tmp_dir" && "$tmp_dir" == /tmp/tmp.* && -d "$tmp_dir" && ! -L "$tmp_dir" ]]; then
    rm -rf -- "$tmp_dir"
  fi
  trap - EXIT
  exit "$rc"
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --public-key) (($# >= 2)) || die "--public-key requires a value"; public_key=$2; shift 2 ;;
    --apply) apply=true; shift ;;
    --install-missing) install_missing=true; shift ;;
    --repair) repair=true; shift ;;
    --allow-passwordless-sudo) allow_sudo=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument" ;;
  esac
done
[[ -n "$public_key" ]] || { usage >&2; exit 2; }
[[ -f "$public_key" && ! -L "$public_key" ]] || die "public key must be a regular file"
grep -q 'PRIVATE KEY' "$public_key" 2>/dev/null && die "private keys are not accepted"
mapfile -t key_lines < "$public_key"
[[ ${#key_lines[@]} -eq 1 && -n "${key_lines[0]}" && "${key_lines[0]}" != *$'\r'* ]] || die "public key file must contain exactly one nonempty line"
read -r key_type key_data _key_comment <<< "${key_lines[0]}" || die "unable to read public key"
[[ "$key_type" == ssh-ed25519 && -n "$key_data" ]] || die "only one ssh-ed25519 public key is accepted"
ssh-keygen -lf "$public_key" >/dev/null 2>&1 || die "invalid public key"

os_id=""
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  os_id=${ID:-}
fi
[[ "$os_id" == debian || "$os_id" == ubuntu ]] || die "only Debian and Ubuntu are supported" 3
missing=()
command -v python3 >/dev/null 2>&1 || missing+=(python3)
command -v sudo >/dev/null 2>&1 || missing+=(sudo)
command -v sshd >/dev/null 2>&1 || missing+=(openssh-server)

account_exists=false
getent passwd ansible >/dev/null 2>&1 && account_exists=true
if [[ "$account_exists" != true && -e /home/ansible ]]; then
  die "existing /home/ansible requires manual review before account creation" 4
fi
partial=false
if [[ "$account_exists" == true ]]; then
  entry=$(getent passwd ansible)
  [[ $(printf '%s' "$entry" | cut -d: -f6) == /home/ansible ]] || partial=true
  [[ -f /home/ansible/.ssh/authorized_keys ]] || partial=true
  [[ $(stat -c '%U:%G:%a' /home/ansible 2>/dev/null || true) == ansible:ansible:700 ]] || partial=true
  [[ $(stat -c '%U:%G:%a' /home/ansible/.ssh 2>/dev/null || true) == ansible:ansible:700 ]] || partial=true
  [[ $(stat -c '%U:%G:%a' /home/ansible/.ssh/authorized_keys 2>/dev/null || true) == ansible:ansible:600 ]] || partial=true
  [[ $(cat /home/ansible/.ssh/authorized_keys 2>/dev/null || true) == "$key_type $key_data" ]] || partial=true
  password_state=$(passwd -S ansible 2>/dev/null | awk '{print $2}' || true)
  [[ "$password_state" == L || "$password_state" == LK ]] || partial=true
  if [[ "$allow_sudo" == true ]]; then
    [[ $(stat -c '%U:%G:%a' /etc/sudoers.d/90-semaphore-ansible 2>/dev/null || true) == root:root:440 ]] || partial=true
    [[ $(cat /etc/sudoers.d/90-semaphore-ansible 2>/dev/null || true) == 'ansible ALL=(ALL:ALL) NOPASSWD: ALL' ]] || partial=true
  fi
fi
[[ "$partial" == false || "$repair" == true ]] || die "partial configuration requires --repair" 4

if ((${#missing[@]})); then
  echo "Missing required packages: ${missing[*]}"
  [[ "$apply" == true && "$install_missing" == true ]] || exit 5
fi
if [[ "$apply" != true ]]; then echo "Preflight complete; changes would require --apply."; exit 0; fi
[[ $EUID -eq 0 ]] || die "--apply requires root" 6
tmp_dir=$(mktemp -d)
chmod 0700 "$tmp_dir"
rollback_needed=true

if ((${#missing[@]})); then
  apt-get update
  simulation="$tmp_dir/install.simulation"
  set +e
  apt-get --simulate --no-remove install --no-install-recommends "${missing[@]}" >| "$simulation" 2>&1
  sim_rc=$?
  set -e
  [[ $sim_rc -eq 0 ]] || die "package installation simulation failed" 7
  ! grep -q '^Remv ' "$simulation" || die "package installation simulation planned removals" 7
  apt-get install --no-install-recommends -y "${missing[@]}"
fi

if [[ "$account_exists" != true ]]; then
  useradd --create-home --shell /bin/bash ansible
  account_created=true
  [[ -d /home/ansible ]] && home_created=true
  managed_paths_touched=true
else
  home_meta=$(stat -c '%u:%g:%a' /home/ansible 2>/dev/null || true)
  if [[ -d /home/ansible/.ssh ]]; then
    ssh_dir_existed=true
    ssh_meta=$(stat -c '%u:%g:%a' /home/ansible/.ssh)
  fi
  managed_paths_touched=true
fi
install -d -o ansible -g ansible -m 0700 /home/ansible
install -d -o ansible -g ansible -m 0700 /home/ansible/.ssh
authorized_keys_correct=false
if [[ -f /home/ansible/.ssh/authorized_keys ]] &&
   [[ $(stat -c '%U:%G:%a' /home/ansible/.ssh/authorized_keys) == ansible:ansible:600 ]] &&
   [[ $(cat /home/ansible/.ssh/authorized_keys) == "$key_type $key_data" ]]; then
  authorized_keys_correct=true
fi
if [[ "$authorized_keys_correct" != true ]]; then
  if [[ -e /home/ansible/.ssh/authorized_keys ]]; then
    authorized_keys_existed=true
    cp -p -- /home/ansible/.ssh/authorized_keys "$tmp_dir/authorized_keys.before"
  fi
  key_pending=$(mktemp /home/ansible/.ssh/.authorized_keys.pending.XXXXXX)
  printf '%s %s\n' "$key_type" "$key_data" >| "$key_pending"
  chown ansible:ansible "$key_pending"
  chmod 0600 "$key_pending"
  ssh-keygen -lf "$key_pending" >/dev/null 2>&1 || die "authorized_keys validation failed" 8
  authorized_keys_touched=true
  mv -f -- "$key_pending" /home/ansible/.ssh/authorized_keys
  key_pending=""
fi
if [[ "$allow_sudo" == true ]]; then
  sudoers=/etc/sudoers.d/90-semaphore-ansible
  sudoers_correct=false
  if [[ -f "$sudoers" ]] &&
     [[ $(stat -c '%U:%G:%a' "$sudoers") == root:root:440 ]] &&
     [[ $(cat "$sudoers") == 'ansible ALL=(ALL:ALL) NOPASSWD: ALL' ]]; then
    sudoers_correct=true
  fi
  if [[ "$sudoers_correct" != true ]]; then
    if [[ -e "$sudoers" ]]; then
      sudoers_existed=true
      cp -p -- "$sudoers" "$tmp_dir/sudoers.before"
    fi
    sudo_pending=$(mktemp /etc/sudoers.d/.90-semaphore-ansible.pending.XXXXXX)
    printf '%s\n' 'ansible ALL=(ALL:ALL) NOPASSWD: ALL' >| "$sudo_pending"
    chmod 0440 "$sudo_pending"; chown root:root "$sudo_pending"
    visudo -cf "$sudo_pending" >/dev/null || die "pending sudoers validation failed" 9
    sudoers_touched=true
    mv -f -- "$sudo_pending" "$sudoers"
    sudo_pending=""
  fi
fi
visudo -cf /etc/sudoers >/dev/null || die "sudoers validation failed" 9
if command -v systemctl >/dev/null 2>&1; then
  systemctl is-active --quiet ssh.service || systemctl is-active --quiet ssh.socket || die "SSH service or socket is not active" 10
fi
password_state=$(passwd -S ansible 2>/dev/null | awk '{print $2}' || true)
if [[ "$password_state" != L && "$password_state" != LK ]]; then passwd -l ansible >/dev/null; fi
rollback_needed=false
echo "Managed host bootstrap completed."
