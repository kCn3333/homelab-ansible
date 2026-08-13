#!/usr/bin/env python3
"""Scan public source paths without printing matched values."""

from __future__ import annotations

import ipaddress
import pathlib
import re
import sys


SECRET = re.compile(r"-----BEGIN (?:OPENSSH|RSA|EC|DSA) PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]+")
IPV4 = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
IPV6 = re.compile(r"(?<![A-Za-z0-9])(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}(?![A-Za-z0-9])")
DOMAIN = re.compile(r"https?://|[A-Za-z0-9-]+\.(?:com|net|org|io|dev|local|lan|internal)(?:[^A-Za-z0-9-]|$)")
MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
FIXTURE_FILE = pathlib.PurePosixPath("tests/test-send-wol.py")
FIXTURE_ADDRESSES = {"0.0.0.0", "127.0.0.1", "::1"}
FIXTURE_MAC_NEGATIVES = {"02:11:22:33:44", "02:11-22:33:44:55"}
PUBLIC_API_GROUPS = ("toolkit.fluxcd.io", "longhorn.io", "postgresql.cnpg.io")


def iter_files(paths: list[str]):
    for raw in paths:
        path = pathlib.Path(raw)
        if path.is_dir():
            yield from (item for item in path.rglob("*") if item.is_file())
        elif path.is_file():
            yield path


def main(argv: list[str]) -> int:
    findings: dict[str, set[str]] = {}
    for path in iter_files(argv):
        if any(part in {"inventory", "inventories", "host_vars", "group_vars"} for part in path.parts):
            findings.setdefault("inventory_path", set()).add("redacted")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = pathlib.PurePosixPath(path.as_posix().removeprefix("./"))
        categories: set[str] = set()
        if SECRET.search(text):
            categories.add("secret-material")
        address_text = "" if relative == pathlib.PurePosixPath("scripts/scan-public-tree.py") else text
        for match in IPV4.finditer(address_text):
            value = match.group(0)
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                categories.add("invalid-ipv4-literal")
                continue
            if any(address in network for network in DOCUMENTATION_NETWORKS):
                continue
            if relative == FIXTURE_FILE and value in FIXTURE_ADDRESSES:
                continue
            categories.add("unexpected-ipv4")
        ipv6_text = MAC.sub("", address_text)
        if relative == FIXTURE_FILE:
            for fixture in FIXTURE_ADDRESSES | FIXTURE_MAC_NEGATIVES:
                ipv6_text = ipv6_text.replace(fixture, "")
        for match in IPV6.finditer(ipv6_text):
            value = match.group(0)
            if relative == FIXTURE_FILE and value in FIXTURE_ADDRESSES:
                continue
            categories.add("unexpected-ipv6")
        domain_text = text
        for api_group in PUBLIC_API_GROUPS:
            domain_text = domain_text.replace(api_group, "public-api-group")
        if DOMAIN.search(domain_text):
            categories.add("unexpected-domain-or-url")
        for category in categories:
            findings.setdefault(category, set()).add(str(relative))

    if findings:
        for category in sorted(findings):
            print(f"PRIVACY_SCAN FAIL category={category}")
        return 1
    print("PRIVACY_SCAN PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
