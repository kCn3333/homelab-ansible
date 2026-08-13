#!/usr/bin/env python3
"""Send validated Wake-on-LAN magic packets using the standard library."""

from __future__ import annotations

import argparse
import ipaddress
import math
import re
import socket
import sys
import time
from collections.abc import Sequence


MAC_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}([:-]))(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}$")


class ValidationError(ValueError):
    """Raised when a command-line value is unsafe or invalid."""


def parse_mac(value: str) -> bytes:
    """Return six MAC bytes after strict unicast-address validation."""
    if not MAC_PATTERN.fullmatch(value):
        raise ValidationError("MAC must contain six hexadecimal octets with one separator style")
    mac = bytes.fromhex(value.replace(":", "").replace("-", ""))
    if mac[0] & 1:
        raise ValidationError("multicast and broadcast MAC addresses are not valid devices")
    return mac


def build_magic_packet(value: str) -> bytes:
    """Build the exact 102-byte Wake-on-LAN payload for a device MAC."""
    mac = parse_mac(value)
    packet = b"\xff" * 6 + mac * 16
    if len(packet) != 102:
        raise RuntimeError("internal error: invalid magic-packet length")
    return packet


def parse_broadcast(value: str) -> str:
    """Accept an IPv4 address suitable for a directed or limited broadcast."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValidationError("broadcast must be a valid IPv4 address") from exc
    if address.version != 4:
        raise ValidationError("broadcast must be an IPv4 address")
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValidationError("broadcast must not be unspecified, loopback, or multicast")
    return str(address)


def bounded_integer(minimum: int, maximum: int):
    """Build an argparse converter for a closed integer interval."""

    def convert(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return convert


def nonnegative_float(value: str) -> float:
    """Parse a finite, non-negative interval."""
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed < 0 or parsed == float("inf") or parsed != parsed:
        raise argparse.ArgumentTypeError("must be a finite non-negative number")
    return parsed


def send_magic_packets(
    mac: str,
    broadcast: str,
    port: int,
    count: int,
    interval: float,
    *,
    socket_factory=None,
    sleep=time.sleep,
) -> int:
    """Validate inputs and send a fixed number of magic packets."""
    packet = build_magic_packet(mac)
    destination = (parse_broadcast(broadcast), port)
    if not 1 <= port <= 65535:
        raise ValidationError("port must be between 1 and 65535")
    if not 1 <= count <= 100:
        raise ValidationError("count must be between 1 and 100")
    if not math.isfinite(interval) or interval < 0:
        raise ValidationError("interval must be a finite non-negative number")

    if socket_factory is None:
        socket_factory = socket.socket
    with socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for index in range(count):
            sent = udp_socket.sendto(packet, destination)
            if sent != len(packet):
                raise OSError("incomplete UDP datagram write")
            if index + 1 < count:
                sleep(interval)
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send Wake-on-LAN magic packets")
    parser.add_argument("--mac", required=True)
    parser.add_argument("--broadcast", required=True, type=parse_broadcast)
    parser.add_argument("--port", type=bounded_integer(1, 65535), default=9)
    parser.add_argument("--count", type=bounded_integer(1, 100), default=3)
    parser.add_argument("--interval", type=nonnegative_float, default=0.2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        sent = send_magic_packets(
            args.mac,
            args.broadcast,
            args.port,
            args.count,
            args.interval,
        )
    except ValidationError as exc:
        parser.error(str(exc))
    except OSError as exc:
        print(f"Wake-on-LAN send failed: {exc}", file=sys.stderr)
        return 2
    print(f"Sent {sent} Wake-on-LAN packet(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
