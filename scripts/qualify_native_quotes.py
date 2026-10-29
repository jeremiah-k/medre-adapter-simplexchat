#!/usr/bin/env python3
"""Validate a redacted *external* SimpleX recipient-observation record.

This tool validates completeness, NOT native evidence authenticity. Actual
peer observations must be produced with independently controlled profiles.
It never opens a native profile, sends a message, or promotes capabilities.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

COMMIT = re.compile(r"^[0-9a-f]{40}$")
CASES = (
    "direct_quote",
    "group_quote",
    "origin_attribution",
    "parent_expired_fallback",
    "restart_no_duplicate",
    "ambiguous_submission_fenced",
)


def template() -> dict[str, Any]:
    return {
        "format": "medre-simplex-native-quote-peer-evidence",
        "version": 1,
        "provider_commit": "",
        "core_commit": "",
        "sdk_version": "7.0.2",
        "sender_profile_tag": "",
        "recipient_profile_tag": "",
        "cases": {
            name: {
                "recipient_observed": False,
                "passed": False,
                "evidence_ref": "",
            }
            for name in CASES
        },
    }


def _printable_tag(value: Any, limit: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= limit
        and value == value.strip()
        and value.isprintable()
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object fields are not accepted")
        result[key] = value
    return result


def validate(
    record: Any, *, expected_core_commit: str, expected_provider_commit: str
) -> tuple[str, ...]:
    """Fail closed on missing/stale or self-contradicting evidence metadata."""
    errors: list[str] = []
    for expected in (expected_core_commit, expected_provider_commit):
        if (
            not isinstance(expected, str)
            or not COMMIT.fullmatch(expected)
            or expected == "0" * 40
        ):
            return (
                "Expected revisions must be non-null immutable 40-character commits",
            )
    if not isinstance(record, dict):
        return ("Evidence must be one object",)
    if set(record) != set(template()):
        errors.append("Evidence must contain exactly the supported fields")
    if record.get("format") != "medre-simplex-native-quote-peer-evidence":
        errors.append("Unsupported evidence format")
    if type(record.get("version")) is not int or record["version"] != 1:
        errors.append("Unsupported evidence version")
    if record.get("sdk_version") != "7.0.2":
        errors.append("SDK version must match the qualified 7.0.2 contract")
    for key in ("provider_commit", "core_commit"):
        if (
            not isinstance(record.get(key), str)
            or not COMMIT.fullmatch(record[key])
            or record[key] == "0" * 40
        ):
            errors.append(f"{key} must identify an immutable 40-character commit")
    if record.get("core_commit") != expected_core_commit:
        errors.append("Core revision does not match the selected qualification target")
    if record.get("provider_commit") != expected_provider_commit:
        errors.append(
            "Provider revision does not match the selected qualification target"
        )
    sender = record.get("sender_profile_tag")
    recipient = record.get("recipient_profile_tag")
    if (
        not isinstance(sender, str)
        or not isinstance(recipient, str)
        or (
            not _printable_tag(sender, 128)
            or not _printable_tag(recipient, 128)
            or sender == recipient
        )
    ):
        errors.append("Two distinct bounded profile tags are required")
    cases = record.get("cases")
    if not isinstance(cases, dict) or set(cases) != set(CASES):
        errors.append("All six required independent-peer cases must be present")
        return tuple(errors)
    for name in CASES:
        case = cases[name]
        if not isinstance(case, dict):
            errors.append(f"{name}: evidence must be an object")
            continue
        if set(case) != {"recipient_observed", "passed", "evidence_ref"}:
            errors.append(f"{name}: unexpected evidence fields")
        if case.get("passed") is not True or case.get("recipient_observed") is not True:
            errors.append(f"{name}: recipient-side pass not independently recorded")
        evidence_ref = case.get("evidence_ref")
        if not _printable_tag(evidence_ref, 512):
            errors.append(f"{name}: bounded printable evidence reference required")
    return tuple(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--template", action="store_true", help="Print an unqualified evidence template"
    )
    mode.add_argument(
        "--validate",
        metavar="FILE",
        type=Path,
        help="Validate an independent-peer observation record",
    )
    parser.add_argument(
        "--core-commit", help="Expected immutable core qualification revision"
    )
    parser.add_argument(
        "--provider-commit", help="Expected immutable provider qualification revision"
    )
    args = parser.parse_args(argv)
    if args.template and (
        args.core_commit is not None or args.provider_commit is not None
    ):
        parser.error("Revision selectors apply only to --validate")
    if args.template:
        print(json.dumps(template(), indent=2))
        return 0
    if any(
        not isinstance(ref, str) or not COMMIT.fullmatch(ref) or ref == "0" * 40
        for ref in (args.core_commit, args.provider_commit)
    ):
        parser.error(
            "--validate requires non-null immutable --core-commit and --provider-commit"
        )
    try:
        path: Path = args.validate
        if not path.is_file():
            raise ValueError("Evidence must be a regular file")
        with path.open("rb") as handle:
            payload = handle.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("Evidence record exceeds the 1 MiB limit")
        record = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        print(f"Evidence not usable: {type(exc).__name__}", file=sys.stderr)
        return 2
    errors = validate(
        record,
        expected_core_commit=args.core_commit,
        expected_provider_commit=args.provider_commit,
    )
    if errors:
        for error in errors:
            print(f"Unqualified: {error}", file=sys.stderr)
        return 1
    print(
        "Record structurally complete for selected revisions; "
        "independent observation authenticity not verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
