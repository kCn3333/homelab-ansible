#!/usr/bin/env python3
"""Focused offline tests for cluster Python helpers."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import unittest

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


class FakeSocket:
    def __init__(self, family, kind):
        self.family, self.kind, self.sent = family, kind, []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def setsockopt(self, level, option, value):
        self.option = (level, option, value)

    def sendto(self, payload, destination):
        self.sent.append((payload, destination))
        return len(payload)


class ClusterScriptTests(unittest.TestCase):
    def test_magic_packet_and_send(self):
        packet = wol.build_magic_packet(MAC)
        self.assertEqual(packet, b"\xff" * 6 + bytes.fromhex("021122334455") * 16)
        sockets, sleeps = [], []

        def factory(family, kind):
            sockets.append(FakeSocket(family, kind))
            return sockets[-1]

        sent = wol.send_magic_packets(MAC, BROADCAST, 9, 3, .2,
                                      socket_factory=factory, sleep=sleeps.append)
        self.assertEqual((sent, sockets[0].family, sockets[0].kind),
                         (3, socket.AF_INET, socket.SOCK_DGRAM))
        self.assertEqual([item[1] for item in sockets[0].sent], [(BROADCAST, 9)] * 3)
        self.assertEqual(sleeps, [.2, .2])

    def test_wol_rejects_unsafe_inputs(self):
        for mac in ("invalid", "01:00:5e:00:00:01", "ff:ff:ff:ff:ff:ff"):
            with self.subTest(mac=mac), self.assertRaises(wol.ValidationError):
                wol.build_magic_packet(mac)
        for address in ("invalid", "::1", "127" + ".0.0.1", "0" + ".0.0.0"):
            with self.subTest(address=address), self.assertRaises(wol.ValidationError):
                wol.parse_broadcast(address)
        for key, value in (("port", 0), ("port", 65536), ("count", 0),
                           ("count", 101), ("interval", -1)):
            args = dict(mac=MAC, broadcast=BROADCAST, port=9,
                        count=1, interval=0, socket_factory=FakeSocket)
            args[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(wol.ValidationError):
                wol.send_magic_packets(**args)

    def check_restore(self, document, success, category=None):
        result = subprocess.run([sys.executable, str(restore)], input=json.dumps(document),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success)
        if category:
            self.assertEqual(result.stderr, f"LONGHORN_RESTORE_ERROR category={category}\n")
            self.assertEqual(result.stdout, "")
        return result

    def test_restore_parser_accepts_safe_state(self):
        document = {"items": [{"status": {"restoreStatus": {
            "replica": {"isRestoring": False, "error": ""}}}}]}
        result = self.check_restore(document, True)
        self.assertEqual(result.stdout, "LONGHORN_RESTORE_SAFE engines=1 entries=1\n")

    def test_restore_parser_fails_closed(self):
        cases = [
            ({"items": [{"status": {"restoreStatus": {
                "private": {"isRestoring": True, "error": ""}}}}]}, "active-restore"),
            ({"items": [{"status": {"restoreStatus": {
                "private": {"isRestoring": False, "error": "private detail"}}}}]}, "restore-error"),
            ({"items": [{"status": {"restoreStatus": []}}]}, "invalid-restore-status"),
            ({}, "invalid-items"),
        ]
        for document, category in cases:
            with self.subTest(category=category):
                self.check_restore(document, False, category)


if __name__ == "__main__":
    unittest.main()
