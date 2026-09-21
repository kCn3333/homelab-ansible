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
        cls.os_text, cls.os = load(
            "cluster/playbooks/maintenance/k3s-os-upgrade.yml")

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

    def test_os_upgrade_scope_order_and_snapshot(self):
        self.assertEqual(self.os[0]["hosts"], "all")
        self.assertTrue(self.os[0]["tasks"][0]["run_once"])
        self.assertEqual(self.os[0]["tasks"][0]["delegate_to"], "localhost")
        approval = self.os[0]["tasks"][0]["ansible.builtin.assert"]["that"]
        scope = "\n".join(approval)
        for value in ("k3s_os_upgrade_confirm", "ansible_limit", "groups.masters",
                      "groups.workers", "groups.k3s_cluster", "groups.k3s_servers",
                      "'k3s_servers' in groups", "unique", "sort",
                      "groups.masters + groups.workers"):
            self.assertIn(value, scope)
        self.assertEqual(self.os[1]["hosts"], "k3s_cluster")
        self.assertEqual(self.os[0]["vars"]["k3s_os_default_order"],
                         ["worker2", "master", "worker1"])
        add_hosts = [task for play in self.os for task in play["tasks"]
                     if "ansible.builtin.add_host" in task]
        self.assertEqual(len(add_hosts), 1)
        self.assertEqual(add_hosts[0]["ansible.builtin.add_host"]["groups"],
                         "k3s_os_upgrade_sequence")
        rolling = next(play for play in self.os if play["name"] == "Upgrade one OS server at a time")
        self.assertEqual((rolling["hosts"], rolling["serial"], rolling["order"],
                         rolling["any_errors_fatal"]),
                         ("k3s_os_upgrade_sequence", 1, "inventory", True))
        snapshot = next(play for play in self.os if "snapshot" in play["name"])
        self.assertEqual(snapshot["hosts"], "master")
        self.assertEqual(snapshot["tasks"][0]["ansible.builtin.meta"], "end_play")
        self.assertIn("k3s_os_maintenance_required", snapshot["tasks"][0]["when"])
        saves = [task for task in snapshot["tasks"] if
                 "ansible.builtin.command" in task and
                 task["ansible.builtin.command"]["argv"][1:3] == ["etcd-snapshot", "save"]]
        self.assertEqual(len(saves), 1)
        self.assertIn("pre-os-upgrade-", str(saves[0]))
        self.assertTrue(any(task.get("ansible.builtin.command", {}).get("argv", [])[1:3]
                            == ["etcd-snapshot", "ls"] for task in snapshot["tasks"]))

    def test_os_upgrade_version_regex_matches_working_k3s_playbook(self):
        os_tasks = self.os[1]["tasks"]
        k3s_tasks = self.upgrade[1]["tasks"]
        for os_name, k3s_name, field in (
                ("Require one parseable K3s version",
                 "Require one parseable installed K3s version", "ansible.builtin.assert"),
                ("Store K3s version", "Store the installed K3s version", "ansible.builtin.set_fact")):
            os_task = next(task for task in os_tasks if task["name"] == os_name)
            k3s_task = next(task for task in k3s_tasks if task["name"] == k3s_name)
            os_expression = (os_task[field]["that"][0] if field == "ansible.builtin.assert"
                             else os_task[field]["k3s_os_version"])
            k3s_expression = (k3s_task[field]["that"][0] if field == "ansible.builtin.assert"
                              else k3s_task[field]["k3s_current_version"])
            os_pattern = re.search(r"regex_findall\('([^']+)'\)", os_expression).group(1)
            k3s_pattern = re.search(r"regex_findall\('([^']+)'\)", k3s_expression).group(1)
            self.assertEqual(os_pattern, k3s_pattern)
            self.assertNotIn(r"\\.", os_pattern)
            self.assertNotIn(r"\\+", os_pattern)

    def test_os_upgrade_json_items_use_key_lookup(self):
        for path in ("cluster/playbooks/maintenance/k3s-os-upgrade.yml",
                     "cluster/tasks/k3s-os-cluster-health.yml",
                     "cluster/tasks/k3s-os-proxy-matrix.yml"):
            with self.subTest(path=path):
                text = (ROOT / path).read_text()
                self.assertNotIn("from_json).items", text)

    def test_os_upgrade_previews_work_before_cordon(self):
        preparation = next(play for play in self.os if play["name"] ==
                           "Prepare OS maintenance on every server")
        self.assertLess(self.os.index(next(play for play in self.os if play["name"] ==
                                          "Require starting API server to kubelet matrix 9 of 9")),
                        self.os.index(preparation))
        self.assertEqual(preparation["hosts"], "k3s_cluster")
        cache, preview, marker, decision = preparation["tasks"]
        self.assertTrue(cache["ansible.builtin.apt"]["update_cache"])
        self.assertEqual(preview["ansible.builtin.apt"]["upgrade"], "dist")
        self.assertTrue(preview["check_mode"])
        self.assertEqual(marker["ansible.builtin.stat"]["path"], "/var/run/reboot-required")
        self.assertIn("k3s_os_apt_preview.changed", decision["ansible.builtin.set_fact"]
                      ["k3s_os_maintenance_required"])
        self.assertIn("k3s_os_reboot_marker_before.stat.exists",
                      decision["ansible.builtin.set_fact"]["k3s_os_maintenance_required"])
        rolling = next(play for play in self.os if play["name"] == "Upgrade one OS server at a time")
        self.assertEqual(rolling["tasks"][0]["when"], "k3s_os_maintenance_required | bool")
        self.assertNotIn("Refresh APT cache", str(rolling))
        summary = self.os[-1]["tasks"][0]["ansible.builtin.debug"]["msg"]
        for value in ("h.k3s_os_maintenance_required", "CURRENT", "SKIPPED"):
            self.assertIn(value, summary)
        self.assertLess(summary.index("{% if h.k3s_os_maintenance_required"),
                        summary.index("h.k3s_os_packages.changed"))
        self.assertLess(summary.index("h.k3s_os_packages.changed"),
                        summary.index("{% else %}"))

    def test_os_upgrade_health_reads_nodes_once_and_logs_names(self):
        health = yaml.safe_load((ROOT / "cluster/tasks/k3s-os-cluster-health.yml").read_text())
        reads = [task for task in health if task.get("ansible.builtin.command", {}).get("argv", [])[:4]
                 == ["/usr/local/bin/k3s", "kubectl", "get", "nodes"]]
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0]["ansible.builtin.command"]["argv"][-1], "--output=json")
        ready = next(task for task in health if task["name"] ==
                     "Require every Node Ready and schedulable")
        self.assertEqual(ready["loop"], "{{ k3s_os_node_names }}")
        self.assertEqual(ready["loop_control"]["label"], "{{ item }}")
        self.assertIn("selectattr('metadata.name', 'equalto', item)",
                      ready["vars"]["k3s_os_matching_nodes"])

    def test_os_upgrade_success_and_failure_boundaries(self):
        rolling = next(play for play in self.os if play["name"] == "Upgrade one OS server at a time")
        boundary = rolling["tasks"][0]
        block = boundary["block"]
        names = [task["name"] for task in block]
        self.assertLess(names.index("Cordon the current node"),
                        names.index("Drain the current node through eviction"))
        self.assertLess(names.index("Wait for local API and embedded etcd"),
                        names.index("Uncordon only after service API Node and Cilium recovery"))
        self.assertIn("--ignore-daemonsets", str(block))
        self.assertIn("--delete-emptydir-data", str(block))
        self.assertEqual(next(task for task in block if task["name"] == "Upgrade Ubuntu packages")
                         ["ansible.builtin.apt"]["upgrade"], "dist")
        reboot = next(task for task in block if "ansible.builtin.reboot" in task)
        self.assertEqual(reboot["when"], "k3s_os_reboot_marker.stat.exists")
        self.assertEqual(next(task for task in block if task["name"] == "Check reboot marker")
                         ["ansible.builtin.stat"]["path"], "/var/run/reboot-required")
        self.assertIn("rescue", boundary)
        self.assertIn("ansible.builtin.fail", boundary["rescue"][-1])
        self.assertFalse(any("uncordon" in task.get("ansible.builtin.command", {}).get("argv", [])
                             for task in boundary["rescue"]))
        self.assertNotIn("always", boundary)
        self.assertNotIn("Report current Node progress", self.os_text)
        for forbidden in ("--force", "--disable-eviction", "ignore_errors", "autoremove"):
            self.assertNotIn(forbidden, self.os_text)
        self.assertIn("k3s-os-proxy-matrix.yml", self.os_text)
        proxy_text = (ROOT / "cluster/tasks/k3s-os-proxy-matrix.yml").read_text()
        self.assertIn("--raw=/api/v1/nodes/{{ item.1 }}/proxy/healthz", proxy_text)
        self.assertIn("groups.k3s_cluster | product(groups.k3s_cluster)", proxy_text)
        self.assertIn("Pod phases limited to `Running` or `Succeeded`",
                      (ROOT / "docs/k3s-os-upgrade.md").read_text())
        health = yaml.safe_load((ROOT / "cluster/tasks/k3s-os-cluster-health.yml").read_text())
        pod_phase = next(task for task in health if task["name"] ==
                         "Require every Pod phase to be Running or Succeeded")
        self.assertIn("rejectattr('status.phase', 'in', ['Running', 'Succeeded'])",
                      pod_phase["ansible.builtin.assert"]["that"][0])
        for path in ("cluster/playbooks/maintenance/k3s-os-upgrade.yml",
                     "cluster/tasks/k3s-os-cluster-health.yml",
                     "cluster/tasks/k3s-os-proxy-matrix.yml"):
            _, parsed = load(path)
            def check(value):
                if isinstance(value, list):
                    for item in value:
                        check(item)
                elif isinstance(value, dict):
                    if "ansible.builtin.command" in value:
                        self.assertIn("changed_when", value)
                        self.assertIn("failed_when", value)
                    for item in value.values():
                        check(item)
            check(parsed)

    def test_os_upgrade_delegate_matrix_longhorn_and_rescue(self):
        self.assertNotIn("ansible.builtin.uri", self.os_text)
        rolling = next(play for play in self.os if play["name"] == "Upgrade one OS server at a time")
        self.assertIn("'worker1' if inventory_hostname == 'master'",
                      rolling["vars"]["k3s_os_kubectl_delegate"])
        boundary = rolling["tasks"][0]
        block = boundary["block"]
        rescue = boundary["rescue"]
        for task in block + rescue:
            command = task.get("ansible.builtin.command", {})
            if "kubectl" in command.get("argv", []) and task["name"] not in (
                    "Wait for local API and embedded etcd",
                    "Probe local API for diagnostic access"):
                self.assertEqual(task["delegate_to"], "{{ k3s_os_kubectl_delegate }}")
        for name, timeout in (("Wait for local API and embedded etcd", "--request-timeout=10s"),
                              ("Probe local API for diagnostic access", "--request-timeout=5s")):
            task = next(task for task in block + rescue if task["name"] == name)
            self.assertIn(timeout, task["ansible.builtin.command"]["argv"])
            self.assertNotIn("delegate_to", task)
        health = yaml.safe_load((ROOT / "cluster/tasks/k3s-os-cluster-health.yml").read_text())
        for task in health:
            command = task.get("ansible.builtin.command", {})
            if "kubectl" in command.get("argv", []):
                self.assertIn("k3s_os_kubectl_delegate", task["delegate_to"])
        longhorn = next(task for task in health if task["name"] == "Read Longhorn volumes")
        self.assertEqual((longhorn["retries"], longhorn["delay"]), (60, 5))
        self.assertIn("status.robustness", longhorn["until"])
        self.assertIn("length > 0", longhorn["until"])

        uncordon = next(i for i, task in enumerate(block) if "uncordon" in
                        task.get("ansible.builtin.command", {}).get("argv", []))
        after = block[uncordon + 1:]
        self.assertEqual([task["ansible.builtin.include_tasks"] for task in after], [
            "../../tasks/k3s-os-cluster-health.yml",
            "../../tasks/k3s-os-proxy-matrix.yml"])
        self.assertEqual(rescue[0]["ansible.builtin.command"]["argv"][2], "cordon")
        self.assertEqual(rescue[0]["delegate_to"], "{{ k3s_os_kubectl_delegate }}")
        self.assertTrue(rescue[0]["ignore_unreachable"])
        self.assertIn("ansible.builtin.fail", rescue[-1])
        self.assertTrue(all(task["ignore_unreachable"] for task in rescue[1:-1]
                            if task["name"] in ("Diagnose K3s service status",
                                                "Diagnose recent K3s journal")))

        proxy = yaml.safe_load((ROOT / "cluster/tasks/k3s-os-proxy-matrix.yml").read_text())[0]
        self.assertEqual(proxy["delegate_to"], "{{ item.0 }}")
        self.assertEqual(proxy["loop"],
                         "{{ groups.k3s_cluster | product(groups.k3s_cluster) | list }}")
        includes = [play for play in self.os if any(
            task.get("ansible.builtin.include_tasks") == "../../tasks/k3s-os-proxy-matrix.yml"
            for task in play.get("tasks", []))]
        self.assertEqual([play["hosts"] for play in includes], ["masters", "masters"])


if __name__ == "__main__":
    unittest.main()
