#!/usr/bin/env python3
"""Structural safety tests for K3s lifecycle playbooks; executes no operations."""

from __future__ import annotations

import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).parents[1]
POWER_ON = ROOT / "cluster/playbooks/power/k3s-power-on.yml"
POWER_OFF = ROOT / "cluster/playbooks/power/k3s-power-off.yml"
HEALTH = ROOT / "cluster/playbooks/audit/k3s-health.yml"


def load(path: pathlib.Path) -> tuple[list[dict], str]:
    text = path.read_text(encoding="utf-8")
    return list(yaml.safe_load_all(text))[0], text


def named_task(play: dict, name: str) -> dict:
    return next(task for task in play.get("tasks", []) if task["name"] == name)


class PlaybookTests(unittest.TestCase):
    path: pathlib.Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.plays, cls.text = load(cls.path)

    def assert_contains_all(self, text: str, values: tuple[str, ...]) -> None:
        for value in values:
            with self.subTest(value=value):
                self.assertIn(value, text)


class PowerOnTests(PlaybookTests):
    path = POWER_ON

    def test_controller_validates_scope_before_wol(self) -> None:
        self.assertEqual("localhost", self.plays[0]["hosts"])
        first = self.plays[0]["tasks"][0]
        assertions = "\n".join(first["ansible.builtin.assert"]["that"])
        self.assert_contains_all(assertions, (
            "ansible_limit", "groups.workers | length == 2",
            "groups.k3s_cluster | length == 3", "groups.k3s_wol_gateway | length == 1",
            "groups.k3s_wol_gateway | intersect(groups.k3s_cluster)",
        ))

    def test_gateway_contract_and_wol_lifecycle(self) -> None:
        gateway = self.plays[1]
        self.assertEqual("k3s_wol_gateway", gateway["hosts"])
        settings = named_task(gateway, "Require private WOL gateway network variables")
        assertions = "\n".join(settings["ansible.builtin.assert"]["that"])
        for required in ("k3s_wol_interface is defined", "k3s_wol_broadcast is defined"):
            self.assertIn(required, assertions)
        lifecycle = named_task(gateway, "Use the existing WOL network profile temporarily")
        names = [task["name"] for task in lifecycle["block"]]
        activate = names.index("Activate the WOL network profile")
        send = names.index("Send Wake-on-LAN to every host before probing SSH")
        self.assertLess(activate, send)
        self.assertEqual(120, lifecycle["block"][activate]["async"])
        send_task = lifecycle["block"][send]
        self.assertEqual("{{ groups.k3s_cluster }}", send_task["loop"])
        self.assertTrue(send_task["no_log"])
        cleanup = lifecycle["always"][0]
        self.assertEqual(
            ["networkctl", "down", "{{ k3s_wol_interface }}"],
            cleanup["ansible.builtin.command"]["argv"],
        )
        self.assertNotIn("failed_when", cleanup)
        self.assertEqual("localhost", self.plays[2]["hosts"])
        self.assertIn("after WOL cleanup", self.plays[2]["name"])

    def test_wol_address_wait_retries_before_send(self) -> None:
        lifecycle = named_task(self.plays[1], "Use the existing WOL network profile temporarily")
        block = lifecycle["block"]
        names = [task["name"] for task in block]
        wait = names.index("Wait for an active WOL interface with a global IPv4 address")
        self.assertLess(names.index("Activate the WOL network profile"), wait)
        self.assertLess(wait, names.index("Send Wake-on-LAN to every host before probing SSH"))
        task = block[wait]
        self.assertEqual(
            "k3s_wol_active_address.rc == 0 "
            "and k3s_wol_active_address.stdout | from_json | length == 1 "
            "and (k3s_wol_active_address.stdout | from_json)[0].addr_info | length > 0",
            task["until"],
        )
        self.assertEqual(30, task["retries"])
        self.assertEqual(2, task["delay"])
        self.assertNotIn("failed_when", task)
        self.assertTrue(task["no_log"])
        self.assertFalse(task["changed_when"])

    def test_recovery_waits_for_service_api_and_etcd(self) -> None:
        self.assert_contains_all(self.text, (
            "Wait for active K3s service", "k3s_service_state.stdout | trim == 'active'",
            "Wait for local API and etcd readiness", "readyz check passed", "[+]etcd ok",
            "Require exact Node membership", "Require every Node to be Ready",
        ))

    def test_power_on_observes_without_service_repair_or_fixed_sleep(self) -> None:
        for play in self.plays[1:]:
            self.assertTrue(play["any_errors_fatal"])
        lowered = self.text.lower()
        self.assertNotIn("ansible.builtin.shell", lowered)
        self.assertNotIn("argv: [sleep", lowered)
        self.assertNotRegex(lowered, r"(?m)^\s+- sleep(?:\s|$)")
        self.assertNotIn("systemctl start", lowered)
        self.assertNotIn("systemctl restart", lowered)
        for private_value in ("192.168.", "vlan5-wol", "ens18", "ens19", "logos"):
            self.assertNotIn(private_value, lowered)
        for forbidden in ("ip link add", "ip address add", "ip link delete"):
            self.assertNotIn(forbidden, lowered)

    def test_node_names_use_one_column_output(self) -> None:
        self.assertIn("--output=custom-columns=NAME:.metadata.name", self.text)
        self.assertNotIn("--output=name", self.text)
        self.assertNotIn("regex_replace', '^node/'", self.text)

    def test_no_scheduling_or_extended_audits(self) -> None:
        lowered = self.text.lower()
        for forbidden in ("uncordon", "cordon", "drain", "flux", "longhorn", "cnpg", "pods", "jobs"):
            self.assertNotIn(forbidden, lowered)


class PowerOffTests(PlaybookTests):
    path = POWER_OFF

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
        self.assertNotIn("k3s_wol_", self.text)
        self.assertNotIn("networkctl", self.text)

    def test_poweroff_wait_boundary_is_linear(self) -> None:
        for play in self.plays[2:]:
            block = play["tasks"][0]["block"]
            schedule, confirm, wait = block[:3]
            self.assertEqual(
                ["systemd-run", "--quiet", "--collect", "--on-active=2s",
                 "systemctl", "poweroff", "--no-block"],
                schedule["ansible.builtin.command"]["argv"],
            )
            self.assertEqual("k3s_poweroff_schedule", schedule["register"])
            self.assertIs(schedule["ignore_unreachable"], True)
            self.assertNotIn("failed_when", schedule)
            self.assertEqual([
                "not (k3s_poweroff_schedule.unreachable | default(false) | bool)",
                "k3s_poweroff_schedule.rc | default(-1) == 0",
            ], confirm["ansible.builtin.assert"]["that"])
            for local_task in (confirm, wait):
                self.assertEqual("localhost", local_task["delegate_to"])
                self.assertIs(local_task["become"], False)
                self.assertNotIn("failed_when", local_task)
            self.assertEqual("stopped", wait["ansible.builtin.wait_for"]["state"])
            self.assertTrue(play["any_errors_fatal"])
            self.assertIn("ansible.builtin.fail", play["tasks"][0]["rescue"][-1])
        self.assertEqual(2, self.text.count("ignore_unreachable:"))
        self.assertNotIn("ignore_errors", self.text)
        self.assertNotIn("ansible.builtin.shell", self.text)
        self.assertNotIn("ansible.builtin.pause", self.text)
        self.assertNotIn("argv: [sleep", self.text)

    def test_no_api_or_rollback_after_first_poweroff(self) -> None:
        boundary = self.text.index("# Safety boundary:")
        tail = self.text[boundary:].lower()
        for forbidden in ("kubectl", "cordon", "drain", "rollback", "uncordon"):
            self.assertNotIn(forbidden, tail)
        self.assertEqual(2, tail.count("systemctl, poweroff, --no-block"))

    def test_storage_and_partial_probe_parsers_are_fail_closed(self) -> None:
        self.assert_contains_all(self.text, (
            "--output=custom-columns=STATE:.status.state", "--output=json",
            "check-longhorn-restore.py", "selectattr('state', 'equalto', 'started')",
            "--output=custom-columns=NAME:.metadata.name",
        ))
        for forbidden in ("restoreStatus[*]", "selectattr('failed'", "rejectattr('failed'", "--output=name"):
            self.assertNotIn(forbidden, self.text)


class HealthTests(PlaybookTests):
    path = HEALTH

    def test_basic_health_excludes_extended_audits(self) -> None:
        lowered = self.text.lower()
        for forbidden in ("flux", "longhorn", "cnpg", "networkpolicy", "backups", "restore", "pods", "jobs"):
            self.assertNotIn(forbidden, lowered)

    def test_report_and_strict_policy_are_explicit(self) -> None:
        self.assertIn("k3s_health_mode in ['report', 'strict']", self.text)
        final_assert = self.plays[-1]["tasks"][-1]["ansible.builtin.assert"]["that"]
        self.assertTrue(final_assert)
        self.assertTrue(all("k3s_health_mode != 'strict' or" in item for item in final_assert))

    def test_probe_node_and_memory_parsers_are_fail_closed(self) -> None:
        self.assert_contains_all(self.text, (
            "item.state == 'started'", "--output=custom-columns=NAME:.metadata.name",
            "get\n          - node", "status.conditions", "memory_mb']['nocache']['used",
            "difference(k3s_live_node_names)", "difference(k3s_expected_node_names)",
        ))
        for forbidden in ("item.failed", "custom-columns=NAME:.metadata.name,READY:", "memfree_mb", "from_json"):
            self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
