# K3s known issues observed on 2026-08-13

This note records observations for later diagnosis. It contains no connection
details or private inventory and does not authorize remediation.

## Confirmed state after the manual worker1 restart

- all three hosts were reachable and K3s was active on every node;
- `worker1` was SSH REACHABLE, K3s was active, had zero failed systemd
  units, and did not require a reboot;
- `worker2` awaits a reboot after a kernel update;
- K3s reported version `v1.34.4+k3s1`;
- local API readiness and etcd checks passed;
- the live Node set matched inventory and every Node was Ready;
- CNPG and Longhorn volumes were healthy;
- there were no active or unknown Longhorn backups;
- recent volume backups were present;
- RAM and root filesystem usage remained below critical thresholds.

## Deferred findings

- the previous `worker1` boot contained CSI Longhorn mount timeouts and
  EXT4 `error -5` warnings for a device presented by the storage layer;
- the `metrics-server` HelmRelease was not Ready;
- two `nginx-test` Pods in the `flux-test` namespace remained Pending;
- the Longhorn `BackupTarget/default` resource was unavailable and requires
  later diagnosis;
- `fwupd.service` and `fwupd-refresh.service` require verification after the
  `worker2` reboot;
- the Power Off validation exposed an invalid JSONPath assumption that treated
  Longhorn Engine `restoreStatus` as a list instead of a map.

The previous-boot storage warnings require a later Longhorn inspection before
Approved Power Off is accepted operationally. This lifecycle correction does
not diagnose or remediate them. BackupTarget unavailability alone must not
block Basic Power Off when no backup or restore operation is active.

## Follow-up

- inspect Longhorn volume and replica health related to the previous `worker1`
  mount timeouts and storage-device errors;
- run `systemctl --failed` and check the reboot-required marker again before a
  later approved shutdown;
- diagnose the `metrics-server` HelmRelease;
- decide whether the `flux-test` namespace and workload are still required;
- recheck Garage connectivity and BackupTarget availability;
- verify `fwupd.service` and `fwupd-refresh.service` after the `worker2` reboot;
- later implement separate GitOps, storage, and workload audit playbooks.
