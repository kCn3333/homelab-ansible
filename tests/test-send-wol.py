#!/usr/bin/env python3
"""Unit tests for the Wake-on-LAN helper; no packets leave the process."""

from __future__ import annotations

import importlib.util
import pathlib
import socket
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock


SCRIPT = pathlib.Path(__file__).parents[1] / "cluster" / "scripts" / "send-wol.py"
SPEC = importlib.util.spec_from_file_location("send_wol", SCRIPT)
assert SPEC and SPEC.loader
send_wol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(send_wol)


class FakeSocket:
    def __init__(self, family: int, kind: int) -> None:
        self.family = family
        self.kind = kind
        self.options: list[tuple[int, int, int]] = []
        self.datagrams: list[tuple[bytes, tuple[str, int]]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.options.append((level, option, value))

    def sendto(self, payload: bytes, destination: tuple[str, int]) -> int:
        self.datagrams.append((payload, destination))
        return len(payload)


class FailingSocket(FakeSocket):
    def __init__(self, family: int, kind: int, fail_at: str) -> None:
        super().__init__(family, kind)
        self.fail_at = fail_at

    def setsockopt(self, level: int, option: int, value: int) -> None:
        if self.fail_at == "setsockopt":
            raise OSError("synthetic setsockopt failure")
        super().setsockopt(level, option, value)

    def sendto(self, payload: bytes, destination: tuple[str, int]) -> int:
        if self.fail_at == "sendto":
            raise OSError("synthetic sendto failure")
        if self.fail_at == "short":
            return len(payload) - 1
        return super().sendto(payload, destination)


class WakeOnLanTests(unittest.TestCase):
    def test_magic_packet_content_and_length(self) -> None:
        packet = send_wol.build_magic_packet("02:11:22:33:44:55")
        self.assertEqual(102, len(packet))
        self.assertEqual(b"\xff" * 6, packet[:6])
        self.assertEqual(bytes.fromhex("021122334455") * 16, packet[6:])

    def test_hyphenated_mac_is_supported(self) -> None:
        self.assertEqual(
            send_wol.build_magic_packet("02:11:22:33:44:55"),
            send_wol.build_magic_packet("02-11-22-33-44-55"),
        )

    def test_invalid_and_mixed_separator_macs_are_rejected(self) -> None:
        for value in ("invalid", "02:11:22:33:44", "02:11-22:33:44:55"):
            with self.subTest(value=value), self.assertRaises(send_wol.ValidationError):
                send_wol.build_magic_packet(value)

    def test_multicast_and_broadcast_macs_are_rejected(self) -> None:
        for value in ("01:00:5e:00:00:01", "ff:ff:ff:ff:ff:ff"):
            with self.subTest(value=value), self.assertRaises(send_wol.ValidationError):
                send_wol.build_magic_packet(value)

    def test_port_boundaries_and_invalid_values(self) -> None:
        for port in (1, 65535):
            with self.subTest(port=port):
                self.assertEqual(
                    1,
                    send_wol.send_magic_packets(
                        "02:11:22:33:44:55", "192.0.2.255", port, 1, 0,
                        socket_factory=FakeSocket,
                    ),
                )
        for port in (0, 65536):
            with self.subTest(port=port), self.assertRaises(send_wol.ValidationError):
                send_wol.send_magic_packets(
                    "02:11:22:33:44:55", "192.0.2.255", port, 1, 0,
                    socket_factory=FakeSocket,
                )

    def test_invalid_broadcast_is_rejected(self) -> None:
        for value in ("not-an-address", "::1", "127.0.0.1", "0.0.0.0"):
            with self.subTest(value=value), self.assertRaises(send_wol.ValidationError):
                send_wol.parse_broadcast(value)

    def test_count_socket_options_and_destination(self) -> None:
        created: list[FakeSocket] = []
        sleeps: list[float] = []

        def factory(family: int, kind: int) -> FakeSocket:
            instance = FakeSocket(family, kind)
            created.append(instance)
            return instance

        result = send_wol.send_magic_packets(
            "02:11:22:33:44:55",
            "192.0.2.255",
            9,
            4,
            0.25,
            socket_factory=factory,
            sleep=sleeps.append,
        )

        self.assertEqual(4, result)
        self.assertEqual(1, len(created))
        self.assertEqual(socket.AF_INET, created[0].family)
        self.assertEqual(socket.SOCK_DGRAM, created[0].kind)
        self.assertIn((socket.SOL_SOCKET, socket.SO_BROADCAST, 1), created[0].options)
        self.assertEqual(4, len(created[0].datagrams))
        self.assertTrue(all(item[1] == ("192.0.2.255", 9) for item in created[0].datagrams))
        self.assertEqual([0.25, 0.25, 0.25], sleeps)

    def test_count_boundaries_and_invalid_values(self) -> None:
        for count in (1, 100):
            created: list[FakeSocket] = []
            factory = lambda family, kind: created.append(FakeSocket(family, kind)) or created[-1]
            self.assertEqual(
                count,
                send_wol.send_magic_packets(
                    "02:11:22:33:44:55", "192.0.2.255", 9, count, 0,
                    socket_factory=factory, sleep=lambda _: None,
                ),
            )
            self.assertEqual(count, len(created[0].datagrams))
        for count in (-1, 0, 101):
            with self.subTest(count=count), self.assertRaises(send_wol.ValidationError):
                send_wol.send_magic_packets(
                    "02:11:22:33:44:55", "192.0.2.255", 9, count, 0,
                    socket_factory=FakeSocket,
                )

    def test_interval_validation_and_sleep_counts(self) -> None:
        for interval in (-1.0, float("nan"), float("inf"), float("-inf")):
            with self.subTest(interval=interval), self.assertRaises(send_wol.ValidationError):
                send_wol.send_magic_packets(
                    "02:11:22:33:44:55", "192.0.2.255", 9, 1, interval,
                    socket_factory=FakeSocket,
                )
        for interval in (0.0, 0.25):
            sleeps: list[float] = []
            send_wol.send_magic_packets(
                "02:11:22:33:44:55", "192.0.2.255", 9, 1, interval,
                socket_factory=FakeSocket, sleep=sleeps.append,
            )
            self.assertEqual([], sleeps)

    def test_socket_failures_and_incomplete_send_are_propagated(self) -> None:
        def socket_failure(_family, _kind):
            raise OSError("synthetic socket failure")

        factories = {
            "socket": socket_failure,
            "setsockopt": lambda family, kind: FailingSocket(family, kind, "setsockopt"),
            "sendto": lambda family, kind: FailingSocket(family, kind, "sendto"),
            "short": lambda family, kind: FailingSocket(family, kind, "short"),
        }
        for name, factory in factories.items():
            with self.subTest(name=name), self.assertRaises(OSError):
                send_wol.send_magic_packets(
                    "02:11:22:33:44:55", "192.0.2.255", 9, 1, 0,
                    socket_factory=factory,
                )

    def test_cli_success_and_validation_exit_codes_without_network(self) -> None:
        with mock.patch.object(send_wol.socket, "socket", FakeSocket), redirect_stdout(StringIO()):
            self.assertEqual(
                0,
                send_wol.main([
                    "--mac", "02:11:22:33:44:55", "--broadcast", "192.0.2.255",
                    "--count", "1", "--interval", "0",
                ]),
            )
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            send_wol.main([
                "--mac", "invalid", "--broadcast", "192.0.2.255",
            ])
        self.assertEqual(2, error.exception.code)

    def test_cli_socket_error_returns_two_without_network(self) -> None:
        with mock.patch.object(send_wol.socket, "socket", side_effect=OSError("synthetic")):
            with redirect_stderr(StringIO()):
                self.assertEqual(
                    2,
                    send_wol.main([
                        "--mac", "02:11:22:33:44:55", "--broadcast", "192.0.2.255",
                    ]),
                )


if __name__ == "__main__":
    unittest.main()
