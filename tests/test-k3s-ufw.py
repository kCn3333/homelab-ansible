#!/usr/bin/env python3
"""Focused offline contract tests for the K3s UFW playbook."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).parents[1]
PATHS = [ROOT / name for name in (
    "cluster/playbooks/power/k3s-ufw.yml", "cluster/tasks/k3s-ufw-inventory.yml",
    "cluster/tasks/k3s-ufw-peer.yml", "cluster/tasks/k3s-ufw-obsolete-check.yml")]
REQUIRED = ("k3s_management_cidr", "k3s_haproxy_ip", "k3s_pod_cidr", "k3s_service_cidr")


class UfwTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = "\n".join(path.read_text() for path in PATHS)
        cls.plays = yaml.safe_load(PATHS[0].read_text())
        cls.tasks = cls.plays[1]["tasks"]

    def test_inventory_only_configuration(self):
        self.assertIn("groups.k3s_cluster", self.text)
        self.assertIn("hostvars[item].ansible_host", self.text)
        self.assertNotIn("k3s_node_cidr", self.text)
        for name in REQUIRED:
            self.assertIn(f"hostvars[item].{name} is defined", self.text)
            self.assertIn(f"hostvars[item].{name} | default('') | trim | length > 0", self.text)
        import re
        self.assertIsNone(re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d+)?\b", self.text))

    def test_safe_module_order_and_rules(self):
        for forbidden in ("ansible.builtin.command:", "ansible.builtin.shell:", "state: reset",
                          "insert:", "state: reloaded", "policy:"):
            self.assertNotIn(forbidden, self.text)
        self.assertEqual(self.tasks[0]["community.general.ufw"]["port"], "22")
        self.assertEqual(self.tasks[1]["loop"], ["80", "443", "6443"])
        self.assertEqual(self.tasks[5]["loop"], "{{ k3s_ufw_obsolete_rules | default([]) }}")
        self.assertTrue(self.tasks[5]["community.general.ufw"]["delete"])
        self.assertEqual(self.tasks[6]["community.general.ufw"], {"state": "enabled"})
        self.assertIn("kubernetes.core.k8s_info", self.tasks[8]["block"][2])
        self.assertTrue(all(play["any_errors_fatal"] for play in self.plays))
        self.assertIn("ansible_limit is not defined or ansible_limit | trim | length == 0", self.text)
        for port in ("6443", "2379", "2380", "10250", "4240", "4244", "8472", "113"):
            self.assertIn(f"port: '{port}'", self.text)

    def test_production_preflight_accepts_complete_inventory_and_rejects_limit(self):
        variables = dict(zip(REQUIRED, ["192.0.2.0/24", "192.0.2.10",
                                        "198.51.100.0/24", "203.0.113.0/24"]))
        inventory = {"all": {"children": {"k3s_cluster": {"vars": variables, "hosts": {
            "node1": {"ansible_host": "192.0.2.1"},
            "node2": {"ansible_host": "192.0.2.2"},
            "node3": {"ansible_host": "192.0.2.3"}}}}}}
        with tempfile.TemporaryDirectory(prefix="k3s-ufw-") as directory:
            directory = Path(directory)
            inventory_path, playbook = directory / "inventory.yml", directory / "preflight.yml"
            inventory_path.write_text(yaml.safe_dump(inventory))
            playbook.write_text(yaml.safe_dump([{"hosts": "localhost", "connection": "local",
                "gather_facts": False, "tasks": [{"ansible.builtin.import_tasks": str(PATHS[1])}]}]))
            command = ["ansible-playbook", "-i", str(inventory_path), str(playbook)]
            env = {**os.environ, "ANSIBLE_LOCAL_TEMP": str(directory / "local")}
            valid = subprocess.run(command, env=env, capture_output=True, text=True)
            limited = subprocess.run(command + ["--limit", "localhost"], env=env,
                                     capture_output=True, text=True)
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            self.assertNotEqual(limited.returncode, 0)


if __name__ == "__main__":
    unittest.main()
