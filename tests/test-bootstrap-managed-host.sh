#!/usr/bin/env bash
set -Eeuo pipefail
command -v docker >/dev/null 2>&1 || { echo "docker is required for bootstrap tests" >&2; exit 77; }
tmp=$(mktemp -d)
cleanup() { [[ "$tmp" == /tmp/tmp.* && -d "$tmp" && ! -L "$tmp" ]] && rm -rf -- "$tmp"; }
trap cleanup EXIT
ssh-keygen -q -t ed25519 -N '' -f "$tmp/key" >/dev/null
ssh-keygen -q -t ed25519 -N '' -f "$tmp/key2" >/dev/null
ssh-keygen -q -t rsa -b 2048 -N '' -f "$tmp/rsa" >/dev/null
cat "$tmp/key.pub" "$tmp/key2.pub" > "$tmp/two.pub"
printf '%s\n' "from=restricted $(cat "$tmp/key.pub")" > "$tmp/options.pub"
: > "$tmp/empty.pub"
# The variables in the single-quoted command are intentionally expanded only
# inside the disposable container.
# shellcheck disable=SC2016
docker run --rm \
  -v "$PWD:/workspace:ro" \
  -v "$tmp:/keys:ro" \
  debian:stable-slim bash -Eeuo pipefail -c '
    apt-get update >/dev/null
    apt-get install -y --no-install-recommends openssh-client >/dev/null
    cd /workspace
    for invalid_key in /keys/key /keys/two.pub /keys/options.pub /keys/rsa.pub /keys/empty.pub; do
      if bash -o noclobber scripts/bootstrap-managed-host.sh --public-key "$invalid_key" >/dev/null 2>&1; then exit 1; fi
    done
    mkdir -p /home/ansible
    touch /home/ansible/preexisting-marker
    if bash -o noclobber scripts/bootstrap-managed-host.sh --public-key /keys/key.pub --apply --install-missing >/dev/null 2>&1; then exit 1; fi
    test -e /home/ansible/preexisting-marker
    rm /home/ansible/preexisting-marker
    rmdir /home/ansible
    set +e
    bash -o noclobber scripts/bootstrap-managed-host.sh --public-key /keys/key.pub
    dry_rc=$?
    set -e
    test "$dry_rc" -eq 5
    test ! -e /home/ansible
    apt-get install -y --no-install-recommends python3 sudo openssh-server passwd >/dev/null
    mkdir /tmp/mock-bin
    printf "%s\n" "#!/usr/bin/env bash" "exit 0" > /tmp/mock-bin/systemctl
    chmod +x /tmp/mock-bin/systemctl
    mkdir /tmp/new-account-fail-bin
    cp /tmp/mock-bin/systemctl /tmp/new-account-fail-bin/systemctl
    printf "%s\n" "#!/usr/bin/env bash" "exit 1" > /tmp/new-account-fail-bin/visudo
    chmod +x /tmp/new-account-fail-bin/*
    if PATH="/tmp/new-account-fail-bin:$PATH" bash -o noclobber scripts/bootstrap-managed-host.sh --public-key /keys/key.pub --apply --allow-passwordless-sudo; then exit 1; fi
    ! getent passwd ansible >/dev/null
    test ! -e /home/ansible
    PATH="/tmp/mock-bin:$PATH" bash -o noclobber scripts/bootstrap-managed-host.sh --public-key /keys/key.pub --apply --allow-passwordless-sudo
    first_key_state=$(stat -c "%U:%G:%a:%Y:%i" /home/ansible/.ssh/authorized_keys)
    first_sudo_state=$(stat -c "%U:%G:%a:%Y:%i" /etc/sudoers.d/90-semaphore-ansible)
    PATH="/tmp/mock-bin:$PATH" bash -o noclobber scripts/bootstrap-managed-host.sh --public-key /keys/key.pub --apply --allow-passwordless-sudo
    test "$(stat -c "%U:%G:%a:%Y:%i" /home/ansible/.ssh/authorized_keys)" = "$first_key_state"
    test "$(stat -c "%U:%G:%a:%Y:%i" /etc/sudoers.d/90-semaphore-ansible)" = "$first_sudo_state"
    chmod 0750 /home/ansible
    chmod 0755 /home/ansible/.ssh
    if PATH="/tmp/mock-bin:$PATH" bash -o noclobber scripts/bootstrap-managed-host.sh --public-key /keys/key.pub --apply --allow-passwordless-sudo; then exit 1; fi
    PATH="/tmp/mock-bin:$PATH" bash -o noclobber scripts/bootstrap-managed-host.sh --public-key /keys/key.pub --apply --repair --allow-passwordless-sudo
    test "$(stat -c "%a" /home/ansible)" = 700
    test "$(stat -c "%a" /home/ansible/.ssh)" = 700

    key_before=$(sha256sum /home/ansible/.ssh/authorized_keys)
    key_meta_before=$(stat -c "%u:%g:%a:%Y" /home/ansible/.ssh/authorized_keys)
    sudo_before=$(sha256sum /etc/sudoers.d/90-semaphore-ansible)
    sudo_meta_before=$(stat -c "%u:%g:%a:%Y" /etc/sudoers.d/90-semaphore-ansible)
    mkdir /tmp/fail-bin
    cp /tmp/mock-bin/systemctl /tmp/fail-bin/systemctl
    printf "%s\n" "#!/usr/bin/env bash" "exit 1" > /tmp/fail-bin/visudo
    chmod +x /tmp/fail-bin/*
    if PATH="/tmp/fail-bin:$PATH" bash -o noclobber scripts/bootstrap-managed-host.sh --public-key /keys/key2.pub --apply --repair --allow-passwordless-sudo; then exit 1; fi
    getent passwd ansible >/dev/null
    test "$(sha256sum /home/ansible/.ssh/authorized_keys)" = "$key_before"
    test "$(stat -c "%u:%g:%a:%Y" /home/ansible/.ssh/authorized_keys)" = "$key_meta_before"
    test "$(sha256sum /etc/sudoers.d/90-semaphore-ansible)" = "$sudo_before"
    test "$(stat -c "%u:%g:%a:%Y" /etc/sudoers.d/90-semaphore-ansible)" = "$sudo_meta_before"
  '
echo "bootstrap-managed-host tests passed"
