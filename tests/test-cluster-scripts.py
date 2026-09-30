#!/usr/bin/env python3
"""Focused offline tests for cluster Python helpers."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, Mock, call

ROOT = Path(__file__).parents[1]


def load(name):
    path = ROOT / "cluster/scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wol = load("send-wol.py")
restore = ROOT / "cluster/scripts/check-longhorn-restore.py"
MAC = "02:11:22:33:44:55"
BROADCAST = "192" + ".0.2.255"


class ClusterScriptTests(unittest.TestCase):
    def test_magic_packet_and_send(self):
        packet = wol.build_magic_packet(MAC)
        self.assertEqual(packet, b"\xff" * 6 + bytes.fromhex("021122334455") * 16)
        factory, sleep = MagicMock(), Mock()
        udp = factory.return_value.__enter__.return_value
        udp.sendto.return_value = len(packet)
        sent = wol.send_magic_packets(MAC, BROADCAST, 9, 3, .2,
                                      socket_factory=factory, sleep=sleep)
        self.assertEqual(sent, 3)
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.assertEqual(udp.sendto.call_args_list, [call(packet, (BROADCAST, 9))] * 3)
        self.assertEqual(sleep.call_args_list, [call(.2)] * 2)

    def test_wol_rejects_unsafe_inputs(self):
        for mac in ("invalid", "01:00:5e:00:00:01", "ff:ff:ff:ff:ff:ff"):
            with self.subTest(mac=mac), self.assertRaises(wol.ValidationError):
                wol.build_magic_packet(mac)
        for address in ("invalid", "::1", "127" + ".0.0.1", "0" + ".0.0.0"):
            with self.subTest(address=address), self.assertRaises(wol.ValidationError):
                wol.parse_broadcast(address)
        for key, value in (("port", 0), ("port", 65536), ("count", 0),
                           ("count", 101), ("interval", -1)):
            factory = Mock()
            args = dict(mac=MAC, broadcast=BROADCAST, port=9,
                        count=1, interval=0, socket_factory=factory)
            args[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(wol.ValidationError):
                wol.send_magic_packets(**args)
            factory.assert_not_called()

    def test_restore_parser(self):
        cases = [
            ({"replica": {"isRestoring": False, "error": ""}}, None),
            ({"private": {"isRestoring": True, "error": ""}}, "active-restore"),
            ({"private": {"isRestoring": False, "error": "private detail"}}, "restore-error"),
            ([], "invalid-restore-status"),
            (None, "invalid-items"),
        ]
        for status, category in cases:
            with self.subTest(category=category):
                document = {} if status is None else {"items": [{"status": {"restoreStatus": status}}]}
                result = subprocess.run([sys.executable, str(restore)], input=json.dumps(document),
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 2 if category else 0)
                self.assertEqual(result.stderr, f"LONGHORN_RESTORE_ERROR category={category}\n" if category else "")
                self.assertEqual(result.stdout, "" if category else "LONGHORN_RESTORE_SAFE engines=1 entries=1\n")


if __name__ == "__main__":
    unittest.main()
