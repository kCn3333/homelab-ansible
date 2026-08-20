#!/usr/bin/env python3
"""Tests for the offline Longhorn restore-state parser."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "cluster/scripts/check-longhorn-restore.py"


def engine(restore_status=...):
    status = {}
    if restore_status is not ...:
        status["restoreStatus"] = restore_status
    return {"status": status}


def run_document(document: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(document),
        text=True,
        capture_output=True,
        check=False,
    )


class LonghornRestoreParserTests(unittest.TestCase):
    def assert_safe(self, document: object, engines: int, entries: int) -> None:
        result = run_document(document)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            f"LONGHORN_RESTORE_SAFE engines={engines} entries={entries}\n",
            result.stdout,
        )
        self.assertEqual("", result.stderr)

    def assert_blocked(self, document: object, category: str) -> None:
        result = run_document(document)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(
            f"LONGHORN_RESTORE_ERROR category={category}\n",
            result.stderr,
        )

    def test_false_restore_entry_is_safe(self) -> None:
        self.assert_safe(
            {"items": [engine({"replica-a": {"isRestoring": False, "error": ""}})]},
            engines=1,
            entries=1,
        )

    def test_multiple_replica_entries_are_validated(self) -> None:
        self.assert_safe(
            {
                "items": [
                    engine(
                        {
                            "replica-a": {"isRestoring": False, "error": ""},
                            "replica-b": {"isRestoring": False},
                        }
                    )
                ]
            },
            engines=1,
            entries=2,
        )

    def test_empty_missing_and_null_restore_status_are_safe(self) -> None:
        self.assert_safe(
            {"items": [engine({}), engine(), engine(None)]},
            engines=3,
            entries=0,
        )

    def test_active_restore_is_blocked_without_leaking_identifiers(self) -> None:
        sensitive = "private-replica-address"
        result = run_document(
            {"items": [engine({sensitive: {"isRestoring": True, "error": ""}})]}
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("LONGHORN_RESTORE_ERROR category=active-restore\n", result.stderr)
        self.assertNotIn(sensitive, result.stdout + result.stderr)

    def test_nonempty_restore_error_is_blocked_without_leaking_content(self) -> None:
        sensitive = "private restore failure details"
        result = run_document(
            {"items": [engine({"replica-a": {"isRestoring": False, "error": sensitive}})]}
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("LONGHORN_RESTORE_ERROR category=restore-error\n", result.stderr)
        self.assertNotIn(sensitive, result.stdout + result.stderr)

    def test_invalid_restore_status_type_is_blocked(self) -> None:
        self.assert_blocked(
            {"items": [engine([])]},
            "invalid-restore-status",
        )

    def test_invalid_map_entry_type_is_blocked(self) -> None:
        self.assert_blocked(
            {"items": [engine({"replica-a": []})]},
            "invalid-entry",
        )

    def test_invalid_is_restoring_and_error_types_are_blocked(self) -> None:
        cases = (
            ({"isRestoring": "false", "error": ""}, "invalid-is-restoring"),
            ({"isRestoring": False, "error": None}, "invalid-error"),
        )
        for entry, category in cases:
            with self.subTest(category=category):
                self.assert_blocked(
                    {"items": [engine({"replica-a": entry})]},
                    category,
                )

    def test_invalid_document_structure_is_blocked(self) -> None:
        cases = (
            ([], "invalid-document"),
            ({}, "invalid-items"),
            ({"items": {}}, "invalid-items"),
            ({"items": [None]}, "invalid-engine"),
            ({"items": [{}]}, "invalid-status"),
        )
        for document, category in cases:
            with self.subTest(category=category):
                self.assert_blocked(document, category)

    def test_invalid_json_is_blocked(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input="not-json",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual("LONGHORN_RESTORE_ERROR category=invalid-json\n", result.stderr)


if __name__ == "__main__":
    unittest.main()
