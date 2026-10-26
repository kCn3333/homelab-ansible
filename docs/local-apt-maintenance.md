# Local APT updates with systemd

For a Debian/Ubuntu host running Semaphore in Docker. The script runs on the
host, so restarting Docker does not interrupt the update process.

It refreshes package lists, runs `apt-get upgrade --no-remove`, cleans downloaded
archives, and reports a reboot marker. It stops on errors and prevents two copies
of the script from running together. It never reboots or removes packages.
There are no secrets or configuration files.

## Install

Exclude the host from Semaphore APT runs: either remove it from the private
`update_standard` and `update_automatic` groups, or set a run/template limit
such as `all:!controller-host`, replacing `controller-host` with its inventory
alias. With a limit, inventory can remain unchanged; apply the exclusion to
every APT template that could select the host. Choose a time without Semaphore jobs:
Docker updates can still interrupt jobs running inside containers. Existing
package holds remain respected; review them if Docker updates were blocked.

From the repository root, run on the target host:

```sh
sudo install -m 0755 scripts/maintain-local-apt.sh /usr/local/sbin/maintain-local-apt
sudo install -m 0644 homelab/systemd/local-apt-maintenance.service /etc/systemd/system/
sudo install -m 0644 homelab/systemd/local-apt-maintenance.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

No environment file is needed. If the earlier version was installed, remove its
unused `/etc/local-apt-maintenance.conf` after replacing the service unit.

## First run and schedule

Start the service manually in a maintenance window and follow the log:

```sh
sudo systemctl start --no-block local-apt-maintenance.service
sudo journalctl -fu local-apt-maintenance.service
```

The update continues after disconnecting SSH. Ctrl-C exits log following only.
Verify the result with:

```sh
sudo systemctl show local-apt-maintenance.service -p Result -p ExecMainStatus
```

After a successful first run, enable the timer:

```sh
sudo systemctl enable --now local-apt-maintenance.timer
systemctl list-timers local-apt-maintenance.timer
```

The example runs on Sunday at 04:55 in the host's timezone. Edit `OnCalendar`
in the timer to change it, then run `sudo systemctl daemon-reload` and
`sudo systemctl restart local-apt-maintenance.timer`. Missed runs are skipped.
Avoid overlapping this schedule with other APT automation.

## Logs and failures

```sh
sudo journalctl -u local-apt-maintenance.service -n 100 --no-pager
sudo systemctl disable --now local-apt-maintenance.timer
```

The last command disables future runs; it does not stop an active update.
Errors mark the service as failed. Inspect the log before retrying. No automatic
rollback or forced timeout is used, to avoid killing dpkg during installation.

Package scripts can restart Docker and other services. `needrestart` lists
additional required service restarts without performing them; review the log
and handle pending restarts manually. A reboot marker is reported in the log,
without sending notifications or rebooting the host.
