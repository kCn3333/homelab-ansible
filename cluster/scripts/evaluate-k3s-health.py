#!/usr/bin/env python3
"""Evaluate supplied K3s health JSON without network or subprocess access."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from typing import Any

CRITICAL_WAITING_REASONS = {
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
    "CreateContainerConfigError", "CreateContainerError", "RunContainerError",
    "InvalidImageName", "ContainerCannotRun", "Unknown",
}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def require_map(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def require_list(value: object, label: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{label} must be {'a nonempty' if nonempty else 'an'} array")
    return value


def require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def parse_timestamp(value: object) -> dt.datetime:
    text = require_text(value, "creationTimestamp")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("creationTimestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("creationTimestamp must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def job_index(document: object) -> dict[tuple[str, str], bool]:
    jobs = require_list(require_map(document, "jobs").get("items"), "jobs.items")
    result: dict[tuple[str, str], bool] = {}
    for raw in jobs:
        job = require_map(raw, "job")
        metadata = require_map(job.get("metadata"), "job.metadata")
        status = require_map(job.get("status"), "job.status")
        namespace = require_text(metadata.get("namespace"), "job.metadata.namespace")
        name = require_text(metadata.get("name"), "job.metadata.name")
        active_raw = status.get("active", 0)
        active = number(active_raw, "job.status.active")
        conditions = require_list(status.get("conditions"), "job.status.conditions", nonempty=True)
        terminal = any(
            require_map(condition, "job condition").get("type") in {"Complete", "Failed"}
            and condition.get("status") == "True"
            for condition in conditions
        )
        result[(namespace, name)] = active == 0 and terminal
    return result


def classify_pods(payload: dict[str, Any], *, pending_grace_seconds: int,
                  clock_skew_seconds: int, now: dt.datetime) -> dict[str, list[dict[str, str]]]:
    pods = require_list(require_map(payload.get("pods"), "pods").get("items"), "pods.items")
    jobs = job_index(payload.get("jobs"))
    critical: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for raw in pods:
        pod = require_map(raw, "pod")
        metadata = require_map(pod.get("metadata"), "pod.metadata")
        status = require_map(pod.get("status"), "pod.status")
        namespace = require_text(metadata.get("namespace"), "pod.metadata.namespace")
        name = require_text(metadata.get("name"), "pod.metadata.name")
        phase = require_text(status.get("phase"), "pod.status.phase")
        identity = f"{namespace}/{name}"
        owners = require_list(metadata.get("ownerReferences", []), "pod.metadata.ownerReferences")
        for key in ("initContainerStatuses", "containerStatuses"):
            statuses = require_list(status.get(key, []), f"pod.status.{key}")
            reasons: set[str] = set()
            for raw_container in statuses:
                container = require_map(raw_container, "container status")
                state = require_map(container.get("state", {}), "container state")
                waiting = state.get("waiting")
                if waiting is not None:
                    reason = require_map(waiting, "container waiting").get("reason")
                    if reason in CRITICAL_WAITING_REASONS:
                        reasons.add(str(reason))
            if reasons:
                critical.append({"pod": identity, "reason": ",".join(sorted(reasons))})
        if any(item["pod"] == identity for item in critical):
            continue
        job_owners = [require_map(owner, "ownerReference") for owner in owners
                      if require_map(owner, "ownerReference").get("kind") == "Job"]
        if phase == "Failed":
            historical = len(job_owners) == 1 and jobs.get(
                (namespace, require_text(job_owners[0].get("name"), "Job owner name")), False
            )
            (warnings if historical else critical).append(
                {"pod": identity, "reason": "HistoricalFailedJob" if historical else "Failed"}
            )
        elif phase == "Pending":
            created = parse_timestamp(metadata.get("creationTimestamp"))
            age = (now - created).total_seconds()
            if age < -clock_skew_seconds:
                critical.append({"pod": identity, "reason": "CreationTimestampInFuture"})
            elif age <= pending_grace_seconds:
                warnings.append({"pod": identity, "reason": "PendingWithinGrace"})
            else:
                critical.append({"pod": identity, "reason": "Pending"})
        elif metadata.get("deletionTimestamp") is not None:
            parse_timestamp(metadata.get("deletionTimestamp"))
            warnings.append({"pod": identity, "reason": "Terminating"})
        elif phase not in {"Running", "Succeeded"}:
            critical.append({"pod": identity, "reason": phase})
    return {"critical": critical, "warnings": warnings}


def ready(resource: dict[str, Any], label: str) -> bool:
    status = require_map(resource.get("status"), f"{label}.status")
    conditions = require_list(status.get("conditions"), f"{label}.status.conditions", nonempty=True)
    return any(require_map(c, "condition").get("type") == "Ready" and c.get("status") == "True" for c in conditions)


def classify_integrations(payload: dict[str, Any]) -> dict[str, Any]:
    expect = require_map(payload.get("expect", {"flux": True, "longhorn": True, "cnpg": True}), "expect")
    for key in ("flux", "longhorn", "cnpg"):
        if not isinstance(expect.get(key), bool):
            raise ValueError(f"expect.{key} must be boolean")
    required = ("flux_git_repositories", "flux_kustomizations", "flux_helm_releases",
                "longhorn_backup_targets", "longhorn_volumes", "longhorn_backups",
                "longhorn_engines", "cnpg_clusters")
    groups = {key: "flux" if key.startswith("flux_") else "longhorn" if key.startswith("longhorn_") else "cnpg" for key in required}
    lists = {key: (require_list(payload.get(key), key, nonempty=key != "longhorn_backups")
                   if expect[groups[key]] else []) for key in required}
    critical: list[dict[str, str]] = []
    for key in required[:3]:
        for item in lists[key]:
            resource = require_map(item, key)
            if not ready(resource, key):
                critical.append({"resource": key, "reason": "NotReady"})
    for raw in lists["longhorn_backup_targets"]:
        item = require_map(raw, "BackupTarget"); status = require_map(item.get("status"), "BackupTarget.status")
        if status.get("available") is not True:
            critical.append({"resource": "longhorn_backup_target", "reason": "Unavailable"})
    for raw in lists["longhorn_volumes"]:
        item = require_map(raw, "Volume"); status = require_map(item.get("status"), "Volume.status")
        robustness = require_text(status.get("robustness"), "Volume.status.robustness")
        if robustness != "healthy" or status.get("restoreRequired", False) is not False:
            critical.append({"resource": "longhorn_volume", "reason": "UnhealthyOrRestoreRequired"})
    for raw in lists["longhorn_backups"]:
        item = require_map(raw, "Backup"); status = require_map(item.get("status"), "Backup.status")
        state = require_text(status.get("state"), "Backup.status.state")
        if state in {"New", "Pending", "InProgress", "Error", "Unknown"}:
            critical.append({"resource": "longhorn_backup", "reason": state})
    for raw in lists["longhorn_engines"]:
        item = require_map(raw, "Engine"); status = require_map(item.get("status"), "Engine.status")
        restore_status = status.get("restoreStatus", {})
        restore_status = require_map(restore_status, "Engine.status.restoreStatus")
        for replica in restore_status.values():
            replica = require_map(replica, "Engine restore replica")
            restoring = replica.get("isRestoring", False)
            error = replica.get("error", "")
            if not isinstance(restoring, bool) or not isinstance(error, str):
                raise ValueError("Engine restore fields have invalid types")
            if restoring or error:
                critical.append({"resource": "longhorn_engine", "reason": "ActiveOrFailedRestore"})
    for raw in lists["cnpg_clusters"]:
        item = require_map(raw, "CNPG"); spec = require_map(item.get("spec"), "CNPG.spec")
        status = require_map(item.get("status"), "CNPG.status")
        instances = number(spec.get("instances"), "CNPG.spec.instances")
        ready_instances = number(status.get("readyInstances"), "CNPG.status.readyInstances")
        primary = require_text(status.get("currentPrimary"), "CNPG.status.currentPrimary")
        if not ready(item, "CNPG") or ready_instances != instances or not primary:
            critical.append({"resource": "cnpg_cluster", "reason": "NotReady"})
    last_backup = {}
    for raw in lists["longhorn_volumes"]:
        item = require_map(raw, "Volume"); metadata = require_map(item.get("metadata"), "Volume.metadata")
        last_backup[require_text(metadata.get("name"), "Volume.metadata.name")] = require_map(item.get("status"), "Volume.status").get("lastBackup") or "none"
    return {"critical": critical, "summary": {"last_backup": last_backup},
            "status": {key: "CHECKED" if value else "NOT_CHECKED" for key, value in expect.items()}}


def classify_metrics(payload: dict[str, Any], memory_warning: float, memory_critical: float,
                     filesystem_warning: float | None = None,
                     filesystem_critical: float | None = None) -> dict[str, Any]:
    filesystem_warning = memory_warning if filesystem_warning is None else filesystem_warning
    filesystem_critical = memory_critical if filesystem_critical is None else filesystem_critical
    if not (0 <= memory_warning <= memory_critical <= 100):
        raise ValueError("memory thresholds must satisfy 0 <= warning <= critical <= 100")
    if not (0 <= filesystem_warning <= filesystem_critical <= 100):
        raise ValueError("filesystem thresholds must satisfy 0 <= warning <= critical <= 100")
    memory = require_map(payload.get("memory_mb"), "memory_mb")
    real = require_map(memory.get("real"), "memory_mb.real")
    nocache = require_map(memory.get("nocache"), "memory_mb.nocache")
    total = number(real.get("total"), "memory total")
    available = number(nocache.get("free"), "memory available")
    if total <= 0 or available < 0 or available > total:
        raise ValueError("memory values are outside valid bounds")
    mounts = require_map(payload.get("mounts"), "mounts")
    expected_ids = require_list(payload.get("expected_mount_ids"), "expected_mount_ids", nonempty=True)
    if any(not isinstance(item, str) or item == "/" or not SAFE_ID.fullmatch(item) for item in expected_ids):
        raise ValueError("filesystem id is invalid")
    if len(set(expected_ids)) != len(expected_ids) or set(mounts) != set(expected_ids) or "root" not in mounts:
        raise ValueError("every expected filesystem must have exactly one result")
    filesystems = []
    for identifier, raw in mounts.items():
        if not isinstance(identifier, str) or identifier == "/" or not SAFE_ID.fullmatch(identifier):
            raise ValueError("filesystem id is invalid")
        item = require_map(raw, f"mount {identifier}")
        mount_total = number(item.get("size_total"), f"mount {identifier} total")
        mount_available = number(item.get("size_available"), f"mount {identifier} available")
        if mount_total <= 0 or mount_available < 0 or mount_available > mount_total:
            raise ValueError("filesystem values are outside valid bounds")
        used = min(100.0, max(0.0, round((mount_total - mount_available) * 100.0 / mount_total, 1)))
        filesystems.append({"id": identifier, "total_bytes": int(mount_total), "available_bytes": int(mount_available), "used_percent": used,
                            "health": "critical" if used >= filesystem_critical else "warning" if used >= filesystem_warning else "ok"})
    memory_used = min(100.0, max(0.0, round((total - available) * 100.0 / total, 1)))
    return {"memory": {"total_mb": int(total), "available_mb": int(available), "used_percent": memory_used,
                       "health": "critical" if memory_used >= memory_critical else "warning" if memory_used >= memory_warning else "ok"},
            "filesystems": filesystems}


def worker_gate_allows(expected: object, results: object) -> bool:
    if not isinstance(expected, list) or any(not isinstance(item, str) for item in expected):
        return False
    if not isinstance(results, dict) or set(results) != set(expected):
        return False
    return all(value is True for value in results.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("pods", "integrations", "metrics", "worker-gate"))
    parser.add_argument("--pending-grace-seconds", type=int, default=300)
    parser.add_argument("--clock-skew-seconds", type=int, default=30)
    parser.add_argument("--warning-percent", type=float, default=80)
    parser.add_argument("--critical-percent", type=float, default=90)
    parser.add_argument("--filesystem-warning-percent", type=float, default=80)
    parser.add_argument("--filesystem-critical-percent", type=float, default=90)
    args = parser.parse_args()
    try:
        payload = require_map(json.load(sys.stdin), "input")
        if args.mode == "pods":
            if args.pending_grace_seconds < 0 or args.clock_skew_seconds < 0:
                raise ValueError("time tolerances must not be negative")
            result = classify_pods(payload, pending_grace_seconds=args.pending_grace_seconds,
                                   clock_skew_seconds=args.clock_skew_seconds,
                                   now=dt.datetime.now(dt.timezone.utc))
        elif args.mode == "integrations":
            result = classify_integrations(payload)
        elif args.mode == "metrics":
            result = classify_metrics(payload, args.warning_percent, args.critical_percent,
                                      args.filesystem_warning_percent,
                                      args.filesystem_critical_percent)
        else:
            result = {"allowed": worker_gate_allows(payload.get("expected"), payload.get("results"))}
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"health input is invalid: {exc}", file=sys.stderr); return 2
    print(json.dumps(result, separators=(",", ":"), sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
