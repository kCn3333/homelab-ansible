#!/usr/bin/env python3
"""Offline contract test for the K3s UFW playbook."""
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).parents[1]
PLAYBOOK = ROOT / "cluster/playbooks/power/k3s-ufw.yml"
REQUIRED_VARS = ("k3s_management_cidr", "k3s_haproxy_ip",
                 "k3s_additional_ingress_ips", "k3s_cluster_cidr", "k3s_pod_cidr")
REQUIRED_PORTS = ("22", "6443", "80", "443", "10250", "2379", "2380",
                  "9100", "4443", "4244", "4240", "8472", "51820", "123", "113")


class UfwContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PLAYBOOK.read_text()
        cls.plays = yaml.safe_load(cls.text)

    def test_play_and_rules(self):
        self.assertEqual(len(self.plays), 1)
        play = self.plays[0]
        self.assertEqual((play["hosts"], play["gather_facts"], play["become"],
                          play["strategy"], play["any_errors_fatal"]),
                         ("k3s_cluster", False, True, "linear", True))
        self.assertEqual(len(play["pre_tasks"]), 1)
        self.assertIn("ansible.builtin.assert", play["pre_tasks"][0])
        self.assertIn("community.general.ufw:", self.text)
        for value in REQUIRED_VARS + REQUIRED_PORTS:
            self.assertIn(value, self.text)

    def test_public_and_ufw_boundaries(self):
        self.assertIsNone(re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d+)?\b", self.text))
        self.assertIsNone(re.search(r"(?i)(?:[a-f0-9]{1,4}:){2,}[a-f0-9:]*", self.text))
        for value in ("kubernetes.core.k8s_info", "k3s_service_cidr",
                      "k3s_ufw_obsolete_rules", "delete", "reset", "reload", "policy",
                      "groups.k3s_cluster",
                      "hostvars[item].ansible_host"):
            self.assertNotIn(value, self.text)


if __name__ == "__main__":
    unittest.main()
