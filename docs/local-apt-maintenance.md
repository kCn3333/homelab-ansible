# Local APT updates with systemd

Runs on the host independently of Docker. Updates packages without removals,
cleans downloaded archives, and reports a required reboot. Never reboots.
Exclude this host from Semaphore APT runs, e.g. with limit `all:!controller-host`
(replace the inventory alias). Schedule outside Semaphore jobs: Docker may restart.

## Install or update

Run from the repository root on the target host:

```sh
sudo install -m 0755 scripts/maintain-local-apt.sh /usr/local/sbin/maintain-local-apt
sudo install -m 0644 homelab/systemd/local-apt-maintenance.service /etc/systemd/system/
sudo install -m 0644 homelab/systemd/local-apt-maintenance.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

## Manual use

```sh
sudo maintain-local-apt --check  # Simulation using cached package lists; no changes.
sudo maintain-local-apt         # Refresh lists, install updates, clean archives.
```

Unknown arguments are rejected. Package holds remain respected.

## Schedule

```sh
sudoedit /etc/systemd/system/local-apt-maintenance.timer
```

Set one `OnCalendar=` line under `[Timer]` (host's local timezone):

| Frequency | Setting |
| --- | --- |
| Sunday at 04:55 (default) | `OnCalendar=Sun *-*-* 04:55` |
| Daily at 03:30 | `OnCalendar=*-*-* 03:30` |
| First day of each month at 04:55 | `OnCalendar=*-*-01 04:55` |

Enable or apply a changed schedule:

```sh
sudo systemctl daemon-reload
sudo systemctl enable local-apt-maintenance.timer
sudo systemctl restart local-apt-maintenance.timer
systemctl list-timers local-apt-maintenance.timer
```

`NEXT` shows the next run. Missed runs are skipped.

## Run, logs, disable

```sh
sudo systemctl start --no-block local-apt-maintenance.service  # Run now.
sudo journalctl -fu local-apt-maintenance.service              # Follow output; Ctrl-C exits.
sudo systemctl show local-apt-maintenance.service -p Result -p ExecMainStatus
sudo systemctl disable --now local-apt-maintenance.timer      # Disable future runs only.
```

Errors fail the service; inspect the journal before retrying. Updates continue
without SSH. Review pending service restarts reported by needrestart and perform
any reboot manually. No notifications or secrets are configured.
