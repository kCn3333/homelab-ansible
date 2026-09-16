#!/usr/bin/env python3
"""Structural safety checks for K3s playbooks; runs no managed-host tasks."""
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).parents[1]


def load(path):
    text = (ROOT / path).read_text()
    return text, yaml.safe_load(text)


def tasks(play):
    wrapper = play["tasks"][0]
    return wrapper.get("block", play["tasks"])


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.on_text, cls.on = load("cluster/playbooks/power/k3s-power-on.yml")
        cls.off_text, cls.off = load("cluster/playbooks/power/k3s-power-off.yml")
        cls.upgrade_text, cls.upgrade = load(
            "cluster/playbooks/maintenance/k3s-upgrade.yml")

    def test_power_on_scope_and_recovery(self):
        self.assertEqual(self.on[0]["hosts"], "localhost")
        scope = "\n".join(tasks(self.on[0])[0]["ansible.builtin.assert"]["that"])
        for value in ("ansible_limit", "groups.workers | length == 2",
                      "groups.k3s_cluster | length == 3",
                      "groups.k3s_wol_gateway | length == 1",
                      "groups.k3s_wol_gateway | intersect(groups.k3s_cluster)"):
            self.assertIn(value, scope)
        for value in ("Wait for active K3s service", "readyz check passed", "[+]etcd ok",
                      "Require exact Node membership", "Require every Node to be Ready"):
            self.assertIn(value, self.on_text)
        self.assertTrue(all(play["any_errors_fatal"] for play in self.on[1:]))

    def test_wol_activation_retry_and_cleanup(self):
        gateway = self.on[1]
        self.assertEqual(gateway["hosts"], "k3s_wol_gateway")
        lifecycle = next(task for task in tasks(gateway)
                         if task["name"] == "Use the existing WOL network profile temporarily")
        active, ready, send = lifecycle["block"]
        self.assertEqual(active["ansible.builtin.command"]["argv"],
                         ["networkctl", "up", "{{ k3s_wol_interface }}"])
        self.assertEqual((ready["retries"], ready["delay"], ready["no_log"]), (30, 2, True))
        self.assertIn("addr_info | length > 0", ready["until"])
        self.assertEqual(send["loop"], "{{ groups.k3s_cluster }}")
        down, verify = lifecycle["always"]
        self.assertEqual(down["ansible.builtin.command"]["argv"],
                         ["networkctl", "down", "{{ k3s_wol_interface }}"])
        self.assertIn("'UP' not in", verify["until"])
        self.assertTrue(down["no_log"] and verify["no_log"])

    def test_every_api_checks_every_kubelet(self):
        play = next(play for play in self.on if play["name"] == "Verify every recovered K3s server")
        check = next(task for task in tasks(play)
                     if task["name"] == "Verify the local API server can proxy to every kubelet")
        self.assertEqual(check["loop"], "{{ groups.k3s_cluster }}")
        self.assertEqual(check["until"], ["k3s_kubelet_proxy.rc == 0",
                                          'k3s_kubelet_proxy.stdout | trim == "ok"'])
        self.assertEqual((check["retries"], check["delay"]), (30, 2))
        self.assertIn("--raw=/api/v1/nodes/{{ item }}/proxy/healthz",
                      check["ansible.builtin.command"]["argv"])
        for key in ("delegate_to", "run_once", "ignore_errors", "ignore_unreachable"):
            self.assertNotIn(key, check)

    def test_power_on_has_no_repairs_private_values_or_extended_audits(self):
        lowered = self.on_text.lower()
        for value in ("ansible.builtin.shell", "argv: [sleep", "systemctl start",
                      "systemctl restart", "ip link add", "ip address add", "ip link delete",
                      "192.168.", "vlan5-wol", "ens18", "ens19", "cordon", "drain",
                      "flux", "longhorn", "cnpg"):
            self.assertNotIn(value, lowered)

    def test_poweroff_scope_order_and_storage_checks(self):
        scope = "\n".join(tasks(self.off[0])[0]["ansible.builtin.assert"]["that"])
        for value in ("k3s_power_action", "k3s_shutdown_confirm", "ansible_limit",
                      "groups.workers | length == 2"):
            self.assertIn(value, scope)
        self.assertEqual([play["hosts"] for play in self.off],
                         ["k3s_cluster", "masters", "workers", "masters"])
        self.assertEqual((self.off[2]["serial"], self.off[2]["order"]), (1, "inventory"))
        for value in ("--output=custom-columns=STATE:.status.state", "--output=json",
                      "check-longhorn-restore.py", "selectattr('state', 'equalto', 'started')"):
            self.assertIn(value, self.off_text)

    def test_poweroff_boundary_is_fail_closed(self):
        expected = ["systemd-run", "--quiet", "--collect", "--on-active=2s",
                    "systemctl", "poweroff", "--no-block"]
        for play in self.off[2:]:
            schedule, confirm, wait = play["tasks"][0]["block"][:3]
            self.assertEqual(schedule["ansible.builtin.command"]["argv"], expected)
            self.assertTrue(schedule["ignore_unreachable"])
            self.assertEqual(confirm["ansible.builtin.assert"]["that"], [
                "not (k3s_poweroff_schedule.unreachable | default(false) | bool)",
                "k3s_poweroff_schedule.rc | default(-1) == 0"])
            self.assertEqual(wait["ansible.builtin.wait_for"]["state"], "stopped")
            self.assertTrue(play["any_errors_fatal"])
        tail = self.off_text[self.off_text.index("# Safety boundary:"):].lower()
        for value in ("kubectl", "cordon", "drain", "rollback", "uncordon"):
            self.assertNotIn(value, tail)
        self.assertEqual(self.off_text.count("ignore_unreachable:"), 2)
        self.assertNotIn("ignore_errors", self.off_text)

    def test_health_policy_remains_narrow_and_explicit(self):
        text, plays = load("cluster/playbooks/audit/k3s-health.yml")
        self.assertIn("k3s_health_mode in ['report', 'strict']", text)
        self.assertIn("k3s_health_mode != 'strict' or", text)
        for value in ("flux", "longhorn", "cnpg", "networkpolicy", "backups", "restore"):
            self.assertNotIn(value, text.lower())
        wrapper = plays[-1]["tasks"][0]
        self.assertEqual(wrapper["always"][0]["ansible.builtin.include_tasks"],
                         "../../tasks/k3s-health-summary.yml")

    def test_upgrade_preflight_is_complete_and_fail_closed(self):
        validation = self.upgrade[0]
        self.assertEqual(validation["hosts"], "k3s_cluster")
        scope = "\n".join(validation["tasks"][0]["ansible.builtin.assert"]["that"])
        for value in ("k3s_target_version", "ansible_limit",
                      "groups.masters | default([]) | length == 1",
                      "groups.workers | default([]) | length == 2",
                      "groups.k3s_cluster | default([]) | length == 3"):
            self.assertIn(value, scope)
        transition = self.upgrade[2]
        transition_text = str(transition)
        for value in ("version(k3s_current_normalized, '>=')",
                      "All K3s servers must start on the same version",
                      "groups.workers + groups.masters"):
            self.assertIn(value, transition_text)
        preflight_text = str(self.upgrade[1])
        for value in ("/usr/local/bin/k3s", "--version", "k3s.service",
                      "--raw=/readyz?verbose", "readyz check passed", "[+]etcd ok",
                      "uname", "x86_64"):
            self.assertIn(value, preflight_text)

        readyz_tasks = [task for task in self.upgrade[1]["tasks"]
                        if task["name"] == "Require local API and etcd readiness"]
        rolling_tasks = self.upgrade[4]["tasks"][0]["block"]
        readyz_tasks += [task for task in rolling_tasks
                         if task["name"] == "Wait for local API and etcd readiness"]
        self.assertEqual(len(readyz_tasks), 2)
        for task in readyz_tasks:
            self.assertIn("--request-timeout=10s",
                          task["ansible.builtin.command"]["argv"])

    def test_upgrade_snapshot_order_and_binary_verification(self):
        snapshot, rolling = self.upgrade[3:5]
        self.assertEqual(snapshot["hosts"], "masters")
        self.assertIn("etcd-snapshot", str(snapshot))
        self.assertIn("pre-k3s-upgrade-", str(snapshot))
        self.assertEqual((rolling["hosts"], rolling["serial"], rolling["order"]),
                         ("k3s_upgrade_order", 1, "inventory"))
        text = str(rolling)
        for value in ("sha256sum-amd64.txt", "checksum", "remote_src", "backup",
                      "k3s.service", "node/{{ inventory_hostname }}"):
            self.assertIn(value, text)
        install = next(task for task in rolling["tasks"][0]["block"]
                       if task["name"] == "Atomically replace the K3s binary and keep a backup")
        self.assertTrue(install["ansible.builtin.copy"]["backup"])
        self.assertEqual(install["ansible.builtin.copy"]["mode"], "0755")
        self.assertEqual(install["register"], "k3s_upgrade_binary_install")
        backup_report = next(task for task in rolling["tasks"][0]["block"]
                             if task["name"] == "Report the retained K3s binary backup")
        self.assertIn("k3s_upgrade_binary_install.backup_file", str(backup_report))

        checksum = next(task for task in rolling["tasks"][0]["block"]
                        if task["name"] == "Extract the official K3s binary checksum")
        expression = checksum["ansible.builtin.set_fact"]["k3s_upgrade_binary_checksums"]
        pattern = re.search(r"regex_findall\('([^']+)'\)", expression).group(1)
        digest = "a" * 64
        self.assertEqual(re.findall(pattern, f"{digest}  k3s"), [digest])

    def test_upgrade_final_checks_cover_membership_and_proxy_matrix(self):
        servers, cluster = self.upgrade[5:]
        for play in (servers, cluster):
            self.assertTrue(all("ansible.builtin.meta" not in task
                                for task in play["tasks"]))
        proxy = next(task for task in servers["tasks"]
                     if task["name"] == "Verify the local API server can proxy to every kubelet")
        self.assertEqual(proxy["loop"], "{{ groups.k3s_cluster }}")
        self.assertEqual(proxy["until"], ["k3s_upgrade_kubelet_proxy.rc == 0",
                                          "k3s_upgrade_kubelet_proxy.stdout | trim == 'ok'"])
        self.assertEqual((proxy["retries"], proxy["delay"]), (30, 2))
        self.assertIn("--raw=/api/v1/nodes/{{ item }}/proxy/healthz",
                      proxy["ansible.builtin.command"]["argv"])
        self.assertIn("Require exact Kubernetes Node membership", str(cluster))
        self.assertIn("Require every Kubernetes Node to be Ready", str(cluster))

    def test_upgrade_does_not_cross_safety_boundaries(self):
        lowered = self.upgrade_text.lower()
        for value in ("get.k3s" + ".io", "k3s_version: latest", "ansible.builtin.shell",
                      "ignore_errors", "execstart", "/etc/rancher/k3s", "token",
                      "kubeconfig", "remotedialer", "cordon", "drain", "cilium",
                      "flux", "kube-proxy", "community.general.ufw"):
            self.assertNotIn(value, lowered)
        self.assertEqual(lowered.count("state: restarted"), 1)


if __name__ == "__main__":
    unittest.main()
