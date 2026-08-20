#!/usr/bin/env python3
"""Structural safety tests for K3s lifecycle playbooks; executes no operations."""

from __future__ import annotations

import pathlib
import re
import unittest

import yaml


ROOT = pathlib.Path(__file__).parents[1]
POWER_ON = ROOT / "cluster/playbooks/power/k3s-power-on.yml"
POWER_OFF = ROOT / "cluster/playbooks/power/k3s-power-off.yml"
HEALTH = ROOT / "cluster/playbooks/audit/k3s-health.yml"


def load(path: pathlib.Path) -> tuple[list[dict], str]:
    text = path.read_text(encoding="utf-8")
    return list(yaml.safe_load_all(text))[0], text


def task_names(play: dict) -> list[str]:
    return [task["name"] for task in play.get("tasks", [])]


class PowerOnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plays, cls.text = load(POWER_ON)

    def test_controller_validates_scope_before_wol(self) -> None:
        self.assertEqual("localhost", self.plays[0]["hosts"])
        first = self.plays[0]["tasks"][0]
        assertions = "\n".join(first["ansible.builtin.assert"]["that"])
        self.assertIn("ansible_limit", assertions)
        self.assertIn("groups.workers | length == 2", assertions)
        self.assertIn("groups.k3s_cluster | length == 3", assertions)

    def test_all_wol_attempts_precede_all_ssh_waits(self) -> None:
        names = task_names(self.plays[0])
        send = names.index("Send Wake-on-LAN to every host before probing SSH")
        wait = names.index("Wait for every SSH port after all Wake-on-LAN attempts")
        final_gate = names.index("Require every Wake-on-LAN send and SSH recovery")
        self.assertLess(send, wait)
        self.assertLess(wait, final_gate)
        self.assertFalse(self.plays[0]["tasks"][send]["failed_when"])
        self.assertTrue(self.plays[0]["tasks"][send]["no_log"])
        gate = self.plays[0]["tasks"][final_gate]["ansible.builtin.assert"]["that"]
        self.assertIn("selectattr('state', 'equalto', 'started')", "\n".join(gate))
        self.assertNotIn("selectattr('failed'", "\n".join(gate))

    def test_node_names_use_one_column_output(self) -> None:
        self.assertIn("--output=custom-columns=NAME:.metadata.name", self.text)
        self.assertNotIn("--output=name", self.text)
        self.assertNotIn("regex_replace', '^node/'", self.text)

    def test_no_scheduling_or_extended_audits(self) -> None:
        lowered = self.text.lower()
        for forbidden in ("uncordon", "cordon", "drain", "flux", "longhorn", "cnpg", "pods", "jobs"):
            self.assertNotIn(forbidden, lowered)


class PowerOffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plays, cls.text = load(POWER_OFF)

    def test_confirmations_limit_and_inventory_are_required(self) -> None:
        assertions = "\n".join(
            self.plays[0]["tasks"][0]["ansible.builtin.assert"]["that"]
        )
        for required in ("k3s_power_action", "k3s_shutdown_confirm", "ansible_limit"):
            self.assertIn(required, assertions)
        self.assertIn("groups.workers | length == 2", assertions)

    def test_workers_are_sequential_and_master_is_last(self) -> None:
        self.assertEqual(["k3s_cluster", "masters", "workers", "masters"], [play["hosts"] for play in self.plays])
        self.assertEqual(1, self.plays[2]["serial"])
        self.assertEqual("inventory", self.plays[2]["order"])

    def test_poweroff_wait_boundary_is_linear(self) -> None:
        for play in self.plays[2:]:
            block = play["tasks"][0]["block"]
            names = [task["name"] for task in block]
            self.assertLess(
                names.index("Request nonblocking system poweroff"),
                names.index("Wait locally until SSH stops responding"),
            )
            command = block[0]["ansible.builtin.command"]["argv"]
            self.assertEqual(["systemctl", "poweroff", "--no-block"], command)
            self.assertTrue(play["any_errors_fatal"])

    def test_no_api_or_rollback_after_first_poweroff(self) -> None:
        boundary = self.text.index("# Safety boundary:")
        tail = self.text[boundary:].lower()
        for forbidden in ("kubectl", "cordon", "drain", "rollback", "uncordon"):
            self.assertNotIn(forbidden, tail)
        self.assertEqual(2, tail.count("systemctl, poweroff, --no-block"))

    def test_storage_and_partial_probe_parsers_are_fail_closed(self) -> None:
        self.assertIn("--output=custom-columns=STATE:.status.state", self.text)
        self.assertIn("--output=json", self.text)
        self.assertIn("check-longhorn-restore.py", self.text)
        self.assertNotIn("restoreStatus[*]", self.text)
        self.assertIn("selectattr('state', 'equalto', 'started')", self.text)
        self.assertNotIn("selectattr('failed'", self.text)
        self.assertNotIn("rejectattr('failed'", self.text)
        self.assertIn("--output=custom-columns=NAME:.metadata.name", self.text)
        self.assertNotIn("--output=name", self.text)
        self.assertNotIn("regex_replace', '^node/'", self.text)


class HealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plays, cls.text = load(HEALTH)

    def test_basic_health_excludes_extended_audits(self) -> None:
        lowered = self.text.lower()
        for forbidden in ("flux", "longhorn", "cnpg", "networkpolicy", "backups", "restore", "pods", "jobs"):
            self.assertNotIn(forbidden, lowered)

    def test_report_and_strict_policy_are_explicit(self) -> None:
        self.assertIn("k3s_health_mode in ['report', 'strict']", self.text)
        final_assert = self.plays[-1]["tasks"][-1]["ansible.builtin.assert"]["that"]
        self.assertTrue(final_assert)
        self.assertTrue(all("k3s_health_mode != 'strict' or" in item for item in final_assert))

    def test_missing_and_unexpected_nodes_use_list_filters(self) -> None:
        self.assertIn("difference(k3s_live_node_names)", self.text)
        self.assertIn("difference(k3s_expected_node_names)", self.text)
        self.assertNotIn("from_json", self.text)

    def test_probe_node_and_memory_parsers_are_fail_closed(self) -> None:
        self.assertIn("item.state == 'started'", self.text)
        self.assertNotIn("item.failed", self.text)
        self.assertIn("--output=custom-columns=NAME:.metadata.name", self.text)
        self.assertNotIn("custom-columns=NAME:.metadata.name,READY:", self.text)
        self.assertIn("get\n          - node", self.text)
        self.assertIn("status.conditions", self.text)
        self.assertIn("memory_mb']['nocache']['used", self.text)
        self.assertNotIn("memfree_mb", self.text)


class LifecycleStaticRegressionTests(unittest.TestCase):
    def test_forbidden_lifecycle_constructs_are_absent(self) -> None:
        combined = ""
        for path in (POWER_ON, POWER_OFF, HEALTH):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("{'\\\\n'}", text)
            combined += text
        self.assertNotIn("selectattr('failed'", combined)
        self.assertNotIn("rejectattr('failed'", combined)
        self.assertNotIn("item.failed", combined)
        self.assertNotIn("memfree_mb", combined)
        self.assertNotIn("from_json", combined)
        self.assertNotIn("ignore_errors", combined)
        self.assertIsNone(
            re.search(r"(?m)^\\s+(?:ansible\\.builtin\\.)?(?:shell|raw):", combined)
        )
        self.assertNotIn("--output=name", combined)
        self.assertNotIn("regex_replace', '^node/'", combined)
        self.assertEqual(
            3, combined.count("--output=custom-columns=NAME:.metadata.name")
        )


if __name__ == "__main__":
    unittest.main()
