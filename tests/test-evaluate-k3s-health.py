#!/usr/bin/env python3
"""Unit tests for local K3s health classifiers."""
from __future__ import annotations
import datetime as dt
import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).parents[1] / "cluster/scripts/evaluate-k3s-health.py"
SPEC = importlib.util.spec_from_file_location("evaluate_k3s_health", SCRIPT)
assert SPEC and SPEC.loader
health = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(health)
NOW = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

def pod(name="pod", phase="Running", owner=None, created="2025-12-31T23:00:00Z"):
    metadata = {"name": name, "namespace": "test", "creationTimestamp": created}
    if owner: metadata["ownerReferences"] = [{"kind": "Job", "name": owner}]
    return {"metadata": metadata, "status": {"phase": phase}}

def job(name="job", active=0, terminal="Complete"):
    return {"metadata": {"name": name, "namespace": "test"},
            "status": {"active": active, "conditions": [{"type": terminal, "status": "True"}]}}

def integration_fixture():
    ready = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
    return {
        "flux_git_repositories": [ready], "flux_kustomizations": [ready],
        "flux_helm_releases": [ready],
        "longhorn_backup_targets": [{"status": {"available": True}}],
        "longhorn_volumes": [{"metadata": {"name": "volume"}, "status": {"robustness": "healthy", "restoreRequired": False, "lastBackup": "backup"}}],
        "longhorn_backups": [], "longhorn_engines": [{"status": {"restoreStatus": {}}}],
        "cnpg_clusters": [{"spec": {"instances": 3}, "status": {"readyInstances": 3, "currentPrimary": "primary", "conditions": [{"type": "Ready", "status": "True"}]}}],
    }

class PodTests(unittest.TestCase):
    def classify(self, pods, jobs=()):
        return health.classify_pods({"pods": {"items": list(pods)}, "jobs": {"items": list(jobs)}}, pending_grace_seconds=300, clock_skew_seconds=30, now=NOW)
    def test_running_and_succeeded_are_neutral(self):
        self.assertEqual({"critical": [], "warnings": []}, self.classify([pod(), pod("done", "Succeeded")]))
    def test_terminal_failed_job_is_warning(self):
        self.assertEqual("HistoricalFailedJob", self.classify([pod(phase="Failed", owner="job")], [job()])["warnings"][0]["reason"])
    def test_active_missing_or_nonterminal_job_is_critical(self):
        for jobs in ([job(active=1)], [], [job(terminal="Suspended")]):
            with self.subTest(jobs=jobs): self.assertEqual("Failed", self.classify([pod(phase="Failed", owner="job")], jobs)["critical"][0]["reason"])
    def test_waiting_reasons_in_both_status_lists(self):
        for field in ("initContainerStatuses", "containerStatuses"):
            for reason in health.CRITICAL_WAITING_REASONS:
                item = pod(); item["status"][field] = [{"state": {"waiting": {"reason": reason}}}]
                with self.subTest(field=field, reason=reason): self.assertTrue(self.classify([item])["critical"])
    def test_pending_time_policy(self):
        recent = pod(phase="Pending", created="2025-12-31T23:59:00Z")
        future = pod("future", "Pending", created="2026-01-01T00:01:00Z")
        invalid = pod("invalid", "Pending", created="bad")
        self.assertTrue(self.classify([recent])["warnings"])
        self.assertEqual("CreationTimestampInFuture", self.classify([future])["critical"][0]["reason"])
        with self.assertRaises(ValueError): self.classify([invalid])
    def test_required_pod_fields_and_types(self):
        mutations = [
            {"metadata": {}, "status": {"phase": "Running"}},
            {"metadata": {"name": "x"}, "status": {"phase": "Running"}},
            {"metadata": {"name": "x", "namespace": "n"}, "status": {}},
            {"metadata": {"name": "x", "namespace": "n", "ownerReferences": {}}, "status": {"phase": "Running"}},
            {"metadata": {"name": "x", "namespace": "n"}, "status": {"phase": "Running", "containerStatuses": {}}},
        ]
        for item in mutations:
            with self.subTest(item=item), self.assertRaises(ValueError): self.classify([item])
    def test_terminating_is_warning(self):
        item=pod(); item["metadata"]["deletionTimestamp"]="2026-01-01T00:00:00Z"
        self.assertEqual("Terminating", self.classify([item])["warnings"][0]["reason"])

class MetricTests(unittest.TestCase):
    def fixture(self):
        return {"memory_mb": {"real": {"total": 1000}, "nocache": {"free": 600}}, "mounts": {"root": {"size_total": 1000, "size_available": 500}}, "expected_mount_ids": ["root"]}
    def test_healthy_metrics_are_not_100_percent(self):
        result=health.classify_metrics(self.fixture(),80,90); self.assertEqual(40.0,result["memory"]["used_percent"]); self.assertEqual("root",result["filesystems"][0]["id"])
    def test_invalid_memory_is_rejected(self):
        fixtures=[{}, {"memory_mb": {}}, {"memory_mb":{"real":{"total":0},"nocache":{"free":0}},"mounts":{"root":{"size_total":1,"size_available":1}}}]
        base=self.fixture()
        for value in (-1,1001,"600"):
            altered={**base,"memory_mb":{"real":{"total":1000},"nocache":{"free":value}}}; fixtures.append(altered)
        for fixture in fixtures:
            with self.subTest(fixture=fixture), self.assertRaises(ValueError): health.classify_metrics(fixture,80,90)
    def test_invalid_root_and_mount_ids_are_rejected(self):
        for mounts in ({}, {"root": {"size_total":0,"size_available":0}}, {"root":{"size_total":1,"size_available":2}}, {"/":{"size_total":1,"size_available":1}}):
            fixture=self.fixture(); fixture["mounts"]=mounts
            with self.subTest(mounts=mounts), self.assertRaises(ValueError): health.classify_metrics(fixture,80,90)

class WorkerGateTests(unittest.TestCase):
    def test_only_exact_boolean_true_map_allows_master(self):
        expected=["worker1","worker2"]
        self.assertTrue(health.worker_gate_allows(expected,{"worker1":True,"worker2":True}))
        for results in ({"worker1":True,"worker2":False},{"worker1":True},{"worker1":True,"worker2":True,"extra":True},None,{"worker1":True,"worker2":1}):
            with self.subTest(results=results): self.assertFalse(health.worker_gate_allows(expected,results))

class IntegrationTests(unittest.TestCase):
    def test_fully_healthy_fixture(self): self.assertEqual([],health.classify_integrations(integration_fixture())["critical"])
    def test_disabled_integrations_are_not_checked(self):
        result=health.classify_integrations({"expect":{"flux":False,"longhorn":False,"cnpg":False}})
        self.assertEqual([],result["critical"]); self.assertEqual({"flux":"NOT_CHECKED","longhorn":"NOT_CHECKED","cnpg":"NOT_CHECKED"},result["status"])
    def test_missing_null_wrong_and_empty_required_input(self):
        for transform in (lambda x:x.pop("cnpg_clusters"), lambda x:x.update(cnpg_clusters=None), lambda x:x.update(flux_kustomizations=[])):
            fixture=integration_fixture(); transform(fixture)
            with self.assertRaises(ValueError): health.classify_integrations(fixture)
    def test_flux_false_unknown_and_missing_condition(self):
        for conditions in ([{"type":"Ready","status":"False"}],[{"type":"Ready","status":"Unknown"}],[]):
            fixture=integration_fixture(); fixture["flux_helm_releases"][0]["status"]["conditions"]=conditions
            if conditions: self.assertTrue(health.classify_integrations(fixture)["critical"])
            else:
                with self.assertRaises(ValueError): health.classify_integrations(fixture)
    def test_cnpg_primary_and_instances(self):
        for change in ({"currentPrimary":""},{"readyInstances":2}):
            fixture=integration_fixture(); fixture["cnpg_clusters"][0]["status"].update(change)
            if change.get("currentPrimary")=="":
                with self.assertRaises(ValueError): health.classify_integrations(fixture)
            else: self.assertTrue(health.classify_integrations(fixture)["critical"])
    def test_longhorn_failures(self):
        cases=[("longhorn_backup_targets","available",False), ("longhorn_volumes","robustness","degraded"), ("longhorn_volumes","robustness","faulted")]
        for key,field,value in cases:
            fixture=integration_fixture(); fixture[key][0]["status"][field]=value
            self.assertTrue(health.classify_integrations(fixture)["critical"])
        for state in ("InProgress",None):
            fixture=integration_fixture(); fixture["longhorn_backups"]=[{"status": {} if state is None else {"state":state}}]
            if state is None:
                with self.assertRaises(ValueError): health.classify_integrations(fixture)
            else: self.assertTrue(health.classify_integrations(fixture)["critical"])
    def test_engine_restore_status(self):
        valid=({}, {"replica":{"isRestoring":False,"error":""}})
        for restore in valid:
            fixture=integration_fixture(); fixture["longhorn_engines"][0]["status"]["restoreStatus"]=restore; self.assertFalse(health.classify_integrations(fixture)["critical"])
        for restore in ({"r":{"isRestoring":True,"error":""}}, {"a":{"isRestoring":False,"error":""},"b":{"isRestoring":True,"error":""}}, {"r":{"isRestoring":False,"error":"failed"}}):
            fixture=integration_fixture(); fixture["longhorn_engines"][0]["status"]["restoreStatus"]=restore; self.assertTrue(health.classify_integrations(fixture)["critical"])
        for restore in ([], {"r": []}):
            fixture=integration_fixture(); fixture["longhorn_engines"][0]["status"]["restoreStatus"]=restore
            with self.assertRaises(ValueError): health.classify_integrations(fixture)
        fixture=integration_fixture(); fixture["longhorn_engines"][0]["status"].pop("restoreStatus"); self.assertFalse(health.classify_integrations(fixture)["critical"])

class PrivacyScannerTests(unittest.TestCase):
    scanner = pathlib.Path(__file__).parents[1] / "scripts/scan-public-tree.py"
    def scan(self, relative, content="safe text"):
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content)
            return subprocess.run([str(self.scanner), str(path)], capture_output=True, text=True, check=False)
    def test_inventory_path_segments_fail_without_path_disclosure(self):
        for path in ("inventory/hosts.yml","inventories/prod/hosts","host_vars/node.yml","group_vars/all.yml"):
            result=self.scan(path); self.assertEqual(1,result.returncode); self.assertEqual("PRIVACY_SCAN FAIL category=inventory_path\n",result.stdout)
    def test_nonsegment_and_safe_paths_pass(self):
        for path in ("docs/inventory-example.md","safe.txt"):
            self.assertEqual(0,self.scan(path).returncode)
    def test_content_threats_fail(self):
        fixtures=("10." + "23.45.67", "private-example" + ".internal",
                  "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
                  "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")
        for content in fixtures:
            with self.subTest(content=content): self.assertEqual(1,self.scan("safe.txt",content).returncode)

if __name__ == "__main__": unittest.main()
