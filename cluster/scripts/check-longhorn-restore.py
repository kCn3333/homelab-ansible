#!/usr/bin/env python3
"""Validate Longhorn Engine restore state from kubectl JSON on stdin."""

from __future__ import annotations

import json
import sys
from typing import NoReturn


def reject(category: str) -> NoReturn:
    """Report only a non-sensitive error category and stop fail-closed."""
    print(f"LONGHORN_RESTORE_ERROR category={category}", file=sys.stderr)
    raise SystemExit(2)


def validate(document: object) -> tuple[int, int]:
    """Return Engine and restore-entry counts when every entry is safe."""
    if not isinstance(document, dict):
        reject("invalid-document")

    items = document.get("items")
    if not isinstance(items, list):
        reject("invalid-items")

    entry_count = 0
    for engine in items:
        if not isinstance(engine, dict):
            reject("invalid-engine")

        status = engine.get("status")
        if not isinstance(status, dict):
            reject("invalid-status")

        restore_status = status.get("restoreStatus")
        if restore_status is None:
            continue
        if not isinstance(restore_status, dict):
            reject("invalid-restore-status")

        for replica, entry in restore_status.items():
            if not isinstance(replica, str) or not replica:
                reject("invalid-replica-key")
            if not isinstance(entry, dict):
                reject("invalid-entry")
            entry_count += 1

            if "isRestoring" in entry:
                is_restoring = entry["isRestoring"]
                if not isinstance(is_restoring, bool):
                    reject("invalid-is-restoring")
                if is_restoring:
                    reject("active-restore")

            if "error" in entry:
                error = entry["error"]
                if not isinstance(error, str):
                    reject("invalid-error")
                if error:
                    reject("restore-error")

    return len(items), entry_count


def main() -> int:
    try:
        document = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        reject("invalid-json")

    engines, entries = validate(document)
    print(f"LONGHORN_RESTORE_SAFE engines={engines} entries={entries}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
