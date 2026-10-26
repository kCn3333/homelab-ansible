#!/usr/bin/env bash
# Run directly on the host so Docker restarts do not interrupt the update.
set -Eeuo pipefail

[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
exec 9>/run/lock/maintain-local-apt.lock
flock -n 9 || { echo 'Another update is already running.' >&2; exit 1; }
report_failure() {
  local rc=$1
  echo "APT maintenance failed (exit $rc)." >&2
  exit "$rc"
}
trap 'report_failure "$?"' ERR

export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
# Package scripts may restart services; needrestart only lists extra restarts.
export NEEDRESTART_MODE=l

echo 'Refreshing package lists...'
apt-get -o APT::Update::Error-Mode=any update

echo 'Installing updates without removing packages...'
apt-get -o DPkg::Lock::Timeout=300 \
  -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
  --assume-yes --no-remove upgrade
apt-get clean

echo 'APT maintenance completed.'
if [[ -e /var/run/reboot-required ]]; then
  echo 'Reboot required. Reboot manually during a maintenance window.'
fi
