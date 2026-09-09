#!/usr/bin/env python3
"""Structural safety tests for K3s lifecycle playbooks; executes no operations."""

from __future__ import annotations

import pathlib
import unittest

from jinja2 import Environment, StrictUndefined

import yaml


ROOT = pathlib.Path(__file__).parents[1]


def named_task(play: dict, name: str) -> dict:
    return next(task for task in play.get("tasks", []) if task["name"] == name)


class PlaybookTests(unittest.TestCase):
    path: pathlib.Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = cls.path.read_text(encoding="utf-8")
        cls.plays = yaml.safe_load(cls.text)

    def assert_contains_all(self, text: str, values: tuple[str, ...]) -> None:
        for value in values:
            with self.subTest(value=value):
                self.assertIn(value, text)


class PowerOnTests(PlaybookTests):
    path = ROOT / "cluster/playbooks/power/k3s-power-on.yml"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.wrappers = [play["tasks"][0] for play in cls.plays]
        for play, wrapper in zip(cls.plays, cls.wrappers):
            play["tasks"] = wrapper["block"] + play["tasks"][1:]

    def test_summary_reports_handled_failures_before_stopping(self) -> None:
        for wrapper in self.wrappers:
            report, stop = wrapper["rescue"]
            self.assertEqual("../../tasks/k3s-power-on-summary.yml", report["ansible.builtin.include_tasks"])
            self.assertTrue(report["run_once"])
            self.assertIn("ansible.builtin.fail", stop)
        self.assertTrue(self.plays[-1]["tasks"][-1]["vars"]["k3s_power_on_complete"])

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
        wait = names.index("Wait for an active WOL interface with a global IPv4 address")
        self.assertLess(activate, wait)
        self.assertLess(wait, send)
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
        self.assertEqual("k3s_wol_deactivation", cleanup["register"])
        down = lifecycle["always"][1]
        self.assertEqual(["ip", "-json", "link", "show", "dev", "{{ k3s_wol_interface }}"],
                         down["ansible.builtin.command"]["argv"])
        self.assertIn("'UP' not in", down["until"])
        self.assertTrue(down["no_log"])
        self.assertNotIn("failed_when", down)
        self.assertEqual("localhost", self.plays[2]["hosts"])
        self.assertIn("after WOL cleanup", self.plays[2]["name"])

        task = lifecycle["block"][wait]
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

    def test_every_api_server_checks_every_kubelet_after_readiness(self) -> None:
        play = next(p for p in self.plays if p["name"] == "Verify every recovered K3s server")
        self.assertEqual("k3s_cluster", play["hosts"])
        names = [task["name"] for task in play["tasks"]]
        task = play["tasks"][names.index("Wait for local API and etcd readiness") + 1]
        self.assertEqual([
            "{{ k3s_binary_path }}", "kubectl", "--request-timeout=5s", "get",
            "--raw=/api/v1/nodes/{{ item }}/proxy/healthz",
        ], task["ansible.builtin.command"]["argv"])
        for key, value in {
            "become": True, "register": "k3s_kubelet_proxy", "changed_when": False,
            "retries": 30, "delay": 2, "loop": "{{ groups.k3s_cluster }}",
            "until": ["k3s_kubelet_proxy.rc == 0", 'k3s_kubelet_proxy.stdout | trim == "ok"'],
            "loop_control": {"label": "{{ inventory_hostname }} API -> {{ item }} kubelet"},
        }.items():
            with self.subTest(key=key):
                self.assertEqual(value, task[key])
        for forbidden in ("delegate_to", "run_once", "ignore_errors", "ignore_unreachable", "failed_when"):
            self.assertNotIn(forbidden, task)

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


class PowerOnSummaryTests(unittest.TestCase):
    def test_summary_success_failure_and_missing_results(self) -> None:
        nodes = ["master", "worker1", "worker2"]
        ok = {"rc": 0, "failed": False}
        hosts = {node: {
            "k3s_connectivity": {"ping": "pong"}, "k3s_sudo": ok,
            "k3s_service_state": ok, "k3s_local_readyz": ok,
            "k3s_kubelet_proxy": {"results": [dict(ok, item=n) for n in nodes]},
        } for node in nodes}
        hosts["localhost"] = {"k3s_ssh_results": {"results": [
            {"item": n, "state": "started"} for n in nodes]}}
        hosts["master"].update(k3s_membership={"changed": False},
                                 k3s_node_ready={"results": [dict(ok, item=n) for n in nodes]})
        hosts["gateway"] = {key: ok for key in (
            "k3s_wol_activation", "k3s_wol_active_address", "k3s_wol_deactivation", "k3s_wol_down")}
        hosts["gateway"]["k3s_wol_send"] = {"results": [dict(ok, item=n) for n in nodes]}
        template = Environment(undefined=StrictUndefined, trim_blocks=True).from_string(
            (ROOT / "cluster/templates/k3s-power-on-summary.j2").read_text())
        def render():
            return template.render(groups={"masters": nodes[:1], "workers": nodes[1:], "k3s_wol_gateway": ["gateway"]},
                                   hostvars=hosts, k3s_power_on_complete=True)
        self.assertIn("All required checks passed.", render())
        for result, expected in (({"rc": 1}, "FAILED"), ({}, "NOT CHECKED"),
                                 ({"rc": 0, "failed": True}, "FAILED")):
            hosts["gateway"]["k3s_wol_down"] = result
            output = render()
            self.assertIn(expected, next(line for line in output.splitlines() if line.startswith("Administratively DOWN")))
            self.assertIn("INCOMPLETE", output)
        hosts["gateway"]["k3s_wol_down"] = ok
        hosts["worker1"]["k3s_kubelet_proxy"]["results"][2] = {
            "item": "worker2", "rc": 0, "failed": True, "stdout": "private details"}
        output = render()
        self.assertIn("INCOMPLETE", output)
        self.assertIn("FAILED", next(line for line in output.splitlines() if line.startswith("worker1")))
        self.assertNotIn("private details", output)
        hosts["worker1"] = {}
        output = render()
        self.assertIn("NOT CHECKED", next(line for line in output.splitlines() if line.startswith("worker1")))
        self.assertIn("INCOMPLETE", output)


class PowerOffTests(PlaybookTests):
    path = ROOT / "cluster/playbooks/power/k3s-power-off.yml"

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

    def test_storage_and_partial_probe_parsers_are_fail_closed(self) -> None:
        self.assert_contains_all(self.text, (
            "--output=custom-columns=STATE:.status.state", "--output=json",
            "check-longhorn-restore.py", "selectattr('state', 'equalto', 'started')",
            "--output=custom-columns=NAME:.metadata.name",
        ))
        for forbidden in ("restoreStatus[*]", "selectattr('failed'", "rejectattr('failed'", "--output=name"):
            self.assertNotIn(forbidden, self.text)


class HealthTests(PlaybookTests):
    path = ROOT / "cluster/playbooks/audit/k3s-health.yml"

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
