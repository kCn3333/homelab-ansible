#!/usr/bin/env python3
"""Offline contract and production inventory-preflight tests; no managed hosts."""
import copy
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / 'cluster/playbooks/power/k3s-ufw.yml'
PREFLIGHT = ROOT / 'cluster/tasks/k3s-ufw-inventory.yml'
PEERS = ROOT / 'cluster/tasks/k3s-ufw-peer.yml'
OBSOLETE = ROOT / 'cluster/tasks/k3s-ufw-obsolete-check.yml'
REQUIRED = ('k3s_management_cidr', 'k3s_haproxy_ip', 'k3s_pod_cidr', 'k3s_service_cidr')


class UfwTests(unittest.TestCase):
    def setUp(self):
        self.plays = yaml.safe_load(PLAYBOOK.read_text())
        self.tasks = self.plays[1]['tasks']
        self.source = '\n'.join(path.read_text() for path in (PLAYBOOK, PREFLIGHT, PEERS, OBSOLETE))

    def test_no_literal_addresses_or_cidrs(self):
        self.assertNotRegex(self.source, r'\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d+)?\b')
        self.assertNotRegex(self.source, r'(?i)(?:[a-f0-9]{1,4}:){2,}[a-f0-9:]*')
        self.assertNotIn('k3s_node_cidr', self.source)

    def test_only_ufw_module_manages_firewall(self):
        self.assertIn('community.general.ufw:', self.source)
        for forbidden in ('ansible.builtin.command:', 'ansible.builtin.shell:',
                          'state: reset', 'ufw reset', 'insert:', 'insert_relative_to:',
                          'ufw delete', 'state: reloaded', 'policy:'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_inventory_is_source_of_truth(self):
        self.assertIn('groups.k3s_cluster', self.source)
        self.assertIn('hostvars[item].ansible_host', PEERS.read_text())
        for name in REQUIRED:
            self.assertIn(f'hostvars[item].{name} is defined', PREFLIGHT.read_text())
            self.assertIn(f"hostvars[item].{name} | default('') | trim | length > 0", PREFLIGHT.read_text())

    def test_complete_inventory_and_fatal_errors(self):
        for play in self.plays:
            self.assertIs(play['any_errors_fatal'], True)
        self.assertEqual(self.plays[1]['strategy'], 'linear')
        self.assertIn('ansible_limit is not defined or ansible_limit | trim | length == 0', PREFLIGHT.read_text())
        self.assertIn("'k3s_cluster' in groups", PREFLIGHT.read_text())
        self.assertIn('ansible.builtin.import_tasks', self.plays[1]['pre_tasks'][0])

    def test_required_ports_sources_and_comments(self):
        ssh = self.tasks[0]['community.general.ufw']
        self.assertEqual((ssh['port'], ssh['proto'], ssh['from_ip']), ('22', 'tcp', '{{ k3s_management_cidr }}'))
        self.assertEqual(self.tasks[1]['loop'], ['80', '443', '6443'])
        peers = yaml.safe_load(PEERS.read_text())
        self.assertEqual([item['port'] for item in peers[0]['loop']], ['6443', '2379', '2380', '10250', '4240', '4244'])
        self.assertEqual(peers[1]['community.general.ufw']['port'], '8472')
        self.assertEqual(peers[1]['community.general.ufw']['proto'], 'udp')
        ident = self.tasks[4]['community.general.ufw']
        self.assertEqual((ident['rule'], ident['port'], ident['log']), ('reject', '113', True))
        for task in self.tasks + peers:
            rule = task.get('community.general.ufw', {})
            if rule.get('rule') in ('allow', 'reject'):
                self.assertIn('comment', rule)

    def test_safe_order_and_explicit_deletions(self):
        deletion = self.tasks[5]
        self.assertEqual(deletion['loop'], '{{ k3s_ufw_obsolete_rules | default([]) }}')
        self.assertIs(deletion['community.general.ufw']['delete'], True)
        self.assertEqual(self.tasks[6]['community.general.ufw'], {'state': 'enabled'})
        self.assertIn('ansible.builtin.assert', self.tasks[7])
        self.assertIn('block', self.tasks[8])
        self.assertIn('kubernetes.core.k8s_info', self.tasks[8]['block'][2])

    def test_production_preflight_locally(self):
        valid_vars = dict(zip(REQUIRED, ['192.0.2.0/24', '192.0.2.10', '198.51.100.0/24', '203.0.113.0/24']))
        base = {'all': {'children': {'k3s_cluster': {'vars': valid_vars, 'hosts': {
            'node1': {'ansible_host': '192.0.2.1'},
            'node2': {'ansible_host': '192.0.2.2'},
            'node3': {'ansible_host': '192.0.2.3'},
        }}}}}
        cases = [('valid', base, [], True), ('limited', base, ['--limit', 'localhost'], False),
                 ('missing-group', {'all': {'hosts': {'localhost': {}}}}, [], False)]
        for key in REQUIRED:
            for value in (None, ''):
                inv = copy.deepcopy(base)
                if value is None:
                    del inv['all']['children']['k3s_cluster']['vars'][key]
                else:
                    inv['all']['children']['k3s_cluster']['vars'][key] = value
                cases.append((f'{key}-{value}', inv, [], False))
        inv = copy.deepcopy(base)
        del inv['all']['children']['k3s_cluster']['hosts']['node3']['ansible_host']
        cases.append(('missing-address', inv, [], False))
        for rule, expected in [({'rule': 'allow', 'from_ip': '198.51.100.0/25'}, True),
                               ({'rule': 'allow', 'from_ip': '198.51.100.0/25', 'insert': 1}, False),
                               ({'rule': 'allow', 'from_ip': valid_vars['k3s_management_cidr']}, False)]:
            inv = copy.deepcopy(base)
            inv['all']['children']['k3s_cluster']['vars']['k3s_ufw_obsolete_rules'] = [rule]
            cases.append(('obsolete-' + str(rule), inv, [], expected))
        with tempfile.TemporaryDirectory(prefix='k3s-ufw-test-') as directory:
            directory = Path(directory)
            play = directory / 'preflight.yml'
            play.write_text(yaml.safe_dump([{
                'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                'tasks': [{'ansible.builtin.import_tasks': str(PREFLIGHT)}],
            }]))
            for label, inventory, args, expected in cases:
                with self.subTest(case=label):
                    path = directory / 'inventory.yml'
                    path.write_text(yaml.safe_dump(inventory))
                    result = subprocess.run(['ansible-playbook', '-i', str(path), str(play), *args],
                                            cwd=ROOT, env={**os.environ, 'ANSIBLE_LOCAL_TEMP': str(directory / 'local')},
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
