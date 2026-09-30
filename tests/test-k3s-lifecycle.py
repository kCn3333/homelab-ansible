#!/usr/bin/env python3
"""Offline regression checks for destructive K3s lifecycle boundaries."""
from pathlib import Path
import re
import unittest

from jinja2 import Environment, StrictUndefined
import yaml

ROOT = Path(__file__).parents[1] / "cluster"


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def named(items, name):
    matches = [item for item in items if item.get("name") == name]
    if len(matches) != 1:
        raise AssertionError(f"Expected exactly one task/play named {name!r}")
    return matches[0]


def tasks(play):
    return play["tasks"][0].get("block", play["tasks"])


def argv(task):
    return task.get("ansible.builtin.command", {}).get("argv", [])


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.on = load("playbooks/power/k3s-power-on.yml")
        cls.off = load("playbooks/power/k3s-power-off.yml")
        cls.upgrade = load("playbooks/maintenance/k3s-upgrade.yml")
        cls.os = load("playbooks/maintenance/k3s-os-upgrade.yml")
        cls.rolling = named(cls.os, "Upgrade one OS server at a time")
        cls.health = load("tasks/k3s-os-cluster-health.yml")
        cls.proxy = load("tasks/k3s-os-proxy-matrix.yml")[0]

    def contains(self, value, *fragments):
        for fragment in fragments:
            self.assertIn(fragment, str(value))

    def test_power_on_scope_and_wol_cleanup(self):
        self.assertEqual(self.on[0]["hosts"], "localhost")
        self.contains(tasks(self.on[0])[0]["ansible.builtin.assert"]["that"],
                      "ansible_limit", "groups.workers | length == 2",
                      "groups.k3s_cluster | length == 3", "groups.k3s_wol_gateway | length == 1",
                      "groups.k3s_wol_gateway | intersect(groups.k3s_cluster)")
        self.assertTrue(all(play["any_errors_fatal"] for play in self.on[1:]))
        self.assertEqual(self.on[1]["hosts"], "k3s_wol_gateway")
        lifecycle = named(tasks(self.on[1]), "Use the existing WOL network profile temporarily")
        active, ready, send = lifecycle["block"]
        self.assertEqual(argv(active), ["networkctl", "up", "{{ k3s_wol_interface }}"])
        self.assertEqual((ready["retries"], ready["delay"], ready["no_log"]), (30, 2, True))
        self.assertIn("addr_info | length > 0", ready["until"])
        self.assertEqual(send["loop"], "{{ groups.k3s_cluster }}")
        down, verify = lifecycle["always"]
        self.assertEqual(argv(down), ["networkctl", "down", "{{ k3s_wol_interface }}"])
        self.assertIn("'UP' not in", verify["until"])
        self.assertTrue(down["no_log"] and verify["no_log"])

    def test_recovery_checks_every_api_to_kubelet_path(self):
        recovered = named(self.on, "Verify every recovered K3s server")
        for play, variable in ((recovered, "k3s_kubelet_proxy"),
                               (self.upgrade[5], "k3s_upgrade_kubelet_proxy")):
            with self.subTest(play=play["name"]):
                check = named(tasks(play), "Verify the local API server can proxy to every kubelet")
                self.assertEqual(check["loop"], "{{ groups.k3s_cluster }}")
                self.assertEqual([s.replace('"', "'") for s in check["until"]],
                                 [f"{variable}.rc == 0", f"{variable}.stdout | trim == 'ok'"])
                self.assertEqual((check["retries"], check["delay"]), (30, 2))
                self.assertIn("--raw=/api/v1/nodes/{{ item }}/proxy/healthz", argv(check))
                self.assertFalse({"delegate_to", "run_once", "ignore_errors",
                                  "ignore_unreachable"} & check.keys())
        for play in self.upgrade[5:]:
            self.assertTrue(all("ansible.builtin.meta" not in task for task in tasks(play)))
        self.contains(self.upgrade[-1], "Require exact Kubernetes Node membership",
                      "Require every Kubernetes Node to be Ready")
        self.contains(self.on, "readyz check passed", "[+]etcd ok",
                      "Require exact Node membership", "Require every Node to be Ready")

    def test_poweroff_scope_order_and_fail_closed_boundary(self):
        self.contains(tasks(self.off[0])[0]["ansible.builtin.assert"]["that"],
                      "k3s_power_action", "k3s_shutdown_confirm", "ansible_limit",
                      "groups.workers | length == 2")
        self.assertEqual([p["hosts"] for p in self.off],
                         ["k3s_cluster", "masters", "workers", "masters"])
        self.assertEqual((self.off[2]["serial"], self.off[2]["order"]), (1, "inventory"))
        self.contains(self.off, "--output=custom-columns=STATE:.status.state", "--output=json",
                      "check-longhorn-restore.py", "selectattr('state', 'equalto', 'started')")
        for play in self.off[2:]:
            schedule, confirm, wait = tasks(play)[:3]
            self.assertEqual(argv(schedule), ["systemd-run", "--quiet", "--collect",
                                             "--on-active=2s", "systemctl", "poweroff", "--no-block"])
            self.assertTrue(schedule["ignore_unreachable"] and play["any_errors_fatal"])
            self.assertEqual(confirm["ansible.builtin.assert"]["that"], [
                "not (k3s_poweroff_schedule.unreachable | default(false) | bool)",
                "k3s_poweroff_schedule.rc | default(-1) == 0"])
            self.assertEqual(wait["ansible.builtin.wait_for"]["state"], "stopped")
        tail = str(self.off[2:]).lower()
        for forbidden in ("kubectl", "cordon", "drain", "rollback", "ignore_errors"):
            self.assertNotIn(forbidden, tail)
        self.assertEqual(str(self.off).count("'ignore_unreachable':"), 2)

    def test_binary_upgrade_preflight_snapshot_and_install(self):
        self.assertEqual(self.upgrade[0]["hosts"], "k3s_cluster")
        self.contains(tasks(self.upgrade[0])[0]["ansible.builtin.assert"]["that"],
                      "k3s_target_version", "ansible_limit",
                      "groups.masters | default([]) | length == 1",
                      "groups.workers | default([]) | length == 2",
                      "groups.k3s_cluster | default([]) | length == 3")
        self.contains(self.upgrade[2], "version(k3s_current_normalized, '>=')",
                      "k3s_current_versions | unique | length == 1", "groups.workers + groups.masters")
        self.contains(self.upgrade[1], "/usr/local/bin/k3s", "--version", "k3s.service", "x86_64")
        snapshot, rolling = self.upgrade[3:5]
        self.assertEqual(snapshot["hosts"], "masters")
        self.contains(snapshot, "etcd-snapshot", "pre-k3s-upgrade-", "k3s_upgrade_required")
        self.assertEqual((rolling["hosts"], rolling["serial"], rolling["order"]),
                         ("k3s_upgrade_order", 1, "inventory"))
        for stage, name in ((tasks(self.upgrade[1]), "Require local API and etcd readiness"),
                            (tasks(rolling), "Wait for local API and etcd readiness")):
            readiness = named(stage, name)
            self.assertIn("--request-timeout=10s", argv(readiness))
            self.contains(readiness, "readyz check passed", "[+]etcd ok")
        self.contains(rolling, "sha256sum-amd64.txt", "checksum", "remote_src", "k3s.service")
        install = named(tasks(rolling), "Atomically replace the K3s binary and keep a backup")
        self.assertTrue(install["ansible.builtin.copy"]["backup"])
        self.assertEqual(install["ansible.builtin.copy"]["mode"], "0755")
        self.assertEqual(install["register"], "k3s_upgrade_binary_install")
        self.contains(named(tasks(rolling), "Report the retained K3s binary backup"),
                      "k3s_upgrade_binary_install.backup_file")
        checksum = named(tasks(rolling), "Extract the official K3s binary checksum")
        expression = checksum["ansible.builtin.set_fact"]["k3s_upgrade_binary_checksums"]
        pattern = re.search(r"regex_findall\('([^']+)'\)", expression).group(1)
        self.assertEqual(re.findall(pattern, f"{'a' * 64}  k3s"), ["a" * 64])

    def test_binary_upgrade_stays_within_binary_only_scope(self):
        for forbidden in ("get.k3s" + ".io", "ansible.builtin.shell", "ignore_errors",
                          "execstart", "/etc/rancher/k3s", "cordon", "drain",
                          "community.general.ufw"):
            self.assertNotIn(forbidden, str(self.upgrade).lower())

    def test_os_scope_snapshot_and_serial_order(self):
        approval = tasks(self.os[0])[0]
        self.assertEqual((self.os[0]["hosts"], approval["run_once"], approval["delegate_to"]),
                         ("all", True, "localhost"))
        self.contains(approval["ansible.builtin.assert"]["that"], "k3s_os_upgrade_confirm",
                      "ansible_limit", "groups.masters", "groups.workers", "groups.k3s_cluster",
                      "groups.k3s_servers", "unique", "sort", "groups.masters + groups.workers")
        self.assertEqual(self.os[1]["hosts"], "k3s_cluster")
        self.assertEqual(self.os[0]["vars"]["k3s_os_default_order"], ["worker2", "master", "worker1"])
        groups = [t["ansible.builtin.add_host"]["groups"] for p in self.os
                  for t in tasks(p) if "ansible.builtin.add_host" in t]
        self.assertEqual(groups, ["k3s_os_upgrade_sequence"])
        self.assertEqual((self.rolling["hosts"], self.rolling["serial"], self.rolling["order"],
                          self.rolling["any_errors_fatal"]),
                         ("k3s_os_upgrade_sequence", 1, "inventory", True))
        snapshot = next(p for p in self.os if "snapshot" in p["name"])
        self.assertEqual(snapshot["hosts"], "master")
        self.assertEqual(tasks(snapshot)[0]["ansible.builtin.meta"], "end_play")
        self.assertIn("k3s_os_maintenance_required", tasks(snapshot)[0]["when"])
        saves = [t for t in tasks(snapshot) if argv(t)[1:3] == ["etcd-snapshot", "save"]]
        self.assertEqual(len(saves), 1)
        self.contains(saves, "pre-os-upgrade-")
        self.assertTrue(any(argv(t)[1:3] == ["etcd-snapshot", "ls"] for t in tasks(snapshot)))

    def test_version_regex_parses_installed_version(self):
        # Exercise validation and stored-value patterns, including escaping regressions.
        for play in (self.os[1], self.upgrade[1]):
            expressions = [value for task in tasks(play) for module in
                           ("ansible.builtin.assert", "ansible.builtin.set_fact")
                           for value in task.get(module, {}).values()]
            patterns = [pattern for value in expressions for pattern in
                        re.findall(r"regex_findall\('([^']+)'\)", " ".join(value)
                                   if isinstance(value, list) else str(value))]
            self.assertEqual(len(patterns), 2)
            for pattern in patterns:
                self.assertEqual(re.findall(pattern, "k3s version v1.34.10+k3s1 (build)"),
                                 ["v1.34.10+k3s1"])

    def test_os_preview_and_noop_report(self):
        preparation = named(self.os, "Prepare OS maintenance on every server")
        self.assertLess(self.os.index(named(self.os, "Require starting API server to kubelet matrix 9 of 9")),
                        self.os.index(preparation))
        self.assertLess(self.os.index(preparation), self.os.index(self.rolling))
        cache, preview, marker, decision = tasks(preparation)
        self.assertTrue(cache["ansible.builtin.apt"]["update_cache"] and preview["check_mode"])
        self.assertEqual(preview["ansible.builtin.apt"]["upgrade"], "dist")
        self.assertEqual(marker["ansible.builtin.stat"]["path"], "/var/run/reboot-required")
        self.contains(decision, "k3s_os_apt_preview.changed", "k3s_os_reboot_marker_before.stat.exists")
        self.assertEqual(self.rolling["tasks"][0]["when"], "k3s_os_maintenance_required | bool")
        summary = tasks(self.os[-1])[0]["ansible.builtin.set_fact"]["k3s_os_result_table"]
        env = Environment(undefined=StrictUndefined, trim_blocks=True)
        env.filters["bool"] = bool
        nodes = ["master", "worker1", "worker2"]
        rendered = env.from_string(summary).render(groups={"k3s_cluster": nodes},
            hostvars={node: {"k3s_os_maintenance_required": False} for node in nodes})
        self.assertEqual(rendered.count("CURRENT"), 3)
        self.assertEqual(rendered.count("SKIPPED"), 21)

    def test_os_recovery_and_rescue(self):
        boundary = self.rolling["tasks"][0]
        block, rescue = boundary["block"], boundary["rescue"]
        names = [t["name"] for t in block]
        self.assertLess(names.index("Cordon the current node"),
                        names.index("Drain the current node through eviction"))
        uncordon = next(i for i, t in enumerate(block) if "uncordon" in argv(t))
        self.assertLess(names.index("Wait for local API and embedded etcd"), uncordon)
        self.contains(block, "--ignore-daemonsets", "--delete-emptydir-data")
        self.assertEqual(named(block, "Upgrade Ubuntu packages")["ansible.builtin.apt"]["upgrade"], "dist")
        reboot = next(t for t in block if "ansible.builtin.reboot" in t)
        self.assertEqual(reboot["when"], "k3s_os_reboot_marker.stat.exists")
        self.assertEqual(named(block, "Check reboot marker")["ansible.builtin.stat"]["path"],
                         "/var/run/reboot-required")
        self.assertNotIn("always", boundary)
        self.assertFalse(any("uncordon" in argv(t) for t in rescue))
        self.assertIn("ansible.builtin.fail", rescue[-1])
        for forbidden in ("--force", "--disable-eviction", "ignore_errors", "autoremove"):
            self.assertNotIn(forbidden, str(self.os))
        self.assertEqual([t["ansible.builtin.include_tasks"] for t in block[uncordon + 1:]],
                         ["../../tasks/k3s-os-cluster-health.yml", "../../tasks/k3s-os-proxy-matrix.yml"])
        self.assertEqual(argv(rescue[0])[2], "cordon")
        for task in rescue[:-1]:
            if task["name"] in (rescue[0]["name"], "Diagnose K3s service status",
                                "Diagnose recent K3s journal"):
                self.assertTrue(task["ignore_unreachable"])

    def test_os_delegation_health_and_proxy_matrix(self):
        self.contains(self.rolling["vars"]["k3s_os_kubectl_delegate"],
                      "'worker1' if inventory_hostname == 'master'")
        boundary = self.rolling["tasks"][0]
        local = {"Wait for local API and embedded etcd": "--request-timeout=10s",
                 "Probe local API for diagnostic access": "--request-timeout=5s"}
        for task in boundary["block"] + boundary["rescue"] + self.health:
            if "kubectl" not in argv(task):
                continue
            if task["name"] in local:
                self.assertIn(local[task["name"]], argv(task))
                self.assertNotIn("delegate_to", task)
            else:
                self.assertIn("k3s_os_kubectl_delegate", task["delegate_to"])
        ready = named(self.health, "Require every Node Ready and schedulable")
        self.assertEqual(ready["loop"], "{{ k3s_os_node_names }}")
        self.contains(ready["vars"], "selectattr('metadata.name', 'equalto', item)")
        longhorn = named(self.health, "Read Longhorn volumes")
        self.assertEqual((longhorn["retries"], longhorn["delay"]), (60, 5))
        self.contains(longhorn["until"], "status.robustness", "length > 0")
        self.contains(named(self.health, "Require every Pod phase to be Running or Succeeded"),
                      "rejectattr('status.phase', 'in', ['Running', 'Succeeded'])")
        self.assertEqual(self.proxy["delegate_to"], "{{ item.0 }}")
        self.assertEqual(self.proxy["loop"], "{{ groups.k3s_cluster | product(groups.k3s_cluster) | list }}")
        self.assertIn("--raw=/api/v1/nodes/{{ item.1 }}/proxy/healthz", argv(self.proxy))
        includes = [p["hosts"] for p in self.os if any(t.get("ansible.builtin.include_tasks") ==
                    "../../tasks/k3s-os-proxy-matrix.yml" for t in p["tasks"])]
        self.assertEqual(includes, ["masters", "masters"])
        for document in (self.os, self.health, [self.proxy]):
            self.assertNotIn("from_json).items", str(document))

    def test_health_report_and_strict_modes(self):
        plays = load("playbooks/audit/k3s-health.yml")
        self.contains(plays, "k3s_health_mode in ['report', 'strict']",
                      "k3s_health_mode != 'strict' or")
        self.assertEqual(plays[-1]["tasks"][0]["always"][0]["ansible.builtin.include_tasks"],
                         "../../tasks/k3s-health-summary.yml")


if __name__ == "__main__":
    unittest.main()
