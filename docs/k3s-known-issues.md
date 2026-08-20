# K3s known issues observed on 2026-08-13

This note records observations for later diagnosis. It contains no connection
details or private inventory and does not authorize remediation.

## Confirmed healthy state

- all three hosts were reachable and K3s was active on every node;
- K3s reported version `v1.34.4+k3s1`;
- local API readiness and etcd checks passed;
- the live Node set matched inventory and every Node was Ready;
- CNPG and Longhorn volumes were healthy;
- there were no active or unknown Longhorn backups;
- recent volume backups were present;
- RAM and root filesystem usage remained below critical thresholds.

## Deferred findings

- `worker1` had two failed systemd units and a reboot-required marker;
- `worker2` had two failed systemd units;
- the `metrics-server` HelmRelease was not Ready;
- two `nginx-test` Pods in the `flux-test` namespace remained Pending;
- the Longhorn `BackupTarget/default` resource reported `available=false`.

A later poweroff and startup may clear the reboot marker. Failed units must be
checked again after the next startup. This lifecycle refactor does not fix any
of these findings. BackupTarget unavailability alone must not block Basic Power
Off when no backup or restore operation is active.

## Follow-up

- run `systemctl --failed` on `worker1` and `worker2` after the next startup;
- check the reboot-required marker again;
- diagnose the `metrics-server` HelmRelease;
- decide whether the `flux-test` namespace and workload are still required;
- recheck Garage connectivity and BackupTarget availability;
- later implement separate GitOps, storage, and workload audit playbooks.
