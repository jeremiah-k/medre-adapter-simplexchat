#!/usr/bin/env python3
"""Two-profile local recipient qualification harness and offline verdict gate.

The live tier drives two independently controlled native profiles on one host
through an operator-provided local messaging server and records a content-free
observation record. The offline ``evaluate`` function refuses incomplete or
self-contradictory records for the selected revisions; it never proves that a
recorded observation is authentic. Raw traces, links, addresses and keys stay
in the operator's private campaign directory and never enter this record.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.metadata
import ipaddress
import json
import logging
import os
import re
import secrets
import signal
import sqlite3
import sys
import time
import tomllib
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FORMAT = "medre-simplex-two-profile-recipient-evidence"
VERSION = 2
CASES = (
    "group_send_observed",
    "native_quote_target",
    "restart_continuation",
    "transient_worker_admission",
)
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CHANNEL = re.compile(r"^group:[1-9][0-9]{0,18}$")
LINK_MARKERS = ("smp://", "simplex://", "https://", "http://", "xftp://")

ATTEMPT_KEYS = {"event_id", "outbox_id", "attempt_number"}
LEDGER_KEYS = {
    "event_id",
    "outbox_id",
    "attempt_number",
    "channel",
    "native_ref",
    "native_item_id",
    "accepted",
}
OBSERVATION_KEYS = {
    "channel",
    "item_id",
    "text_sha256",
    "sender_member_sha256",
    "quoted_item_id",
    "quoted_text_sha256",
    "observed_ns",
}
SENDER_WORKER_KEYS = {
    "pid",
    "ready_ns",
    "exited_ns",
    "exit_signal",
    "redrive_pid",
    "redrive_ready_ns",
    "redrive_started_ns",
    "stopped_ns",
}
RECIPIENT_WORKER_KEYS = {"pid", "ready_ns", "stopped_ns"}
TOP_KEYS = {
    "format",
    "version",
    "core_commit",
    "provider_commit",
    "sdk_version",
    "adapter_id",
    "sender_profile_tag",
    "recipient_profile_tag",
    "sender_worker",
    "recipient_worker",
    "target_channel",
    "recipient_target_channel",
    "sibling_channels",
    "recipient_sibling_channels",
    "roster_member_sha256s",
    "servers_local_only",
    "provider_src_matches_pin",
    "harness_commit",
    "cases",
}
CASE_KEYS: dict[str, set[str]] = {
    "group_send_observed": {
        "passed",
        "sent_text_sha256",
        "send_ns",
        "ledger",
        "expected_attempt",
        "observations",
        "sibling_observations",
    },
    "native_quote_target": {
        "passed",
        "sent_text_sha256",
        "send_ns",
        "ledger",
        "expected_attempt",
        "observations",
        "sibling_observations",
        "parent_item_id",
        "parent_text_sha256",
    },
    "restart_continuation": {
        "passed",
        "redrive_ns",
        "kill_ns",
        "reused_native_ref",
        "ledger",
        "reply_text_sha256",
        "reply_observations_total",
        "reply_retained_after_redrive",
        "new_sends",
    },
    "transient_worker_admission": {
        "passed",
        "sent_text_sha256",
        "recipient_send_ns",
        "journal_entry",
    },
}


def _pinned_sdk() -> str:
    """Qualification records must name the SDK version pyproject pins."""
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["project"]
    pins = [r for r in project["dependencies"] if r.startswith("simplex-chat==")]
    if len(pins) != 1:
        raise SystemExit("simplex-chat must be exact-pinned in pyproject.toml")
    return pins[0].partition("==")[2]


def check_installed_sdk() -> str:
    """Verify the local campaign environment, not a remote peer's SDK."""
    try:
        actual = importlib.metadata.version("simplex-chat")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError("simplex-chat is not installed") from exc
    if actual != _pinned_sdk():
        raise ValueError("Installed simplex-chat does not match the declared pin")
    return actual


def _printable_tag(value: Any, limit: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= limit
        and value == value.strip()
        and value.isprintable()
        and "@" not in value
        and not any(marker in value for marker in LINK_MARKERS)
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object fields are not accepted")
        result[key] = value
    return result


def template() -> dict[str, Any]:
    """Return the deliberately unqualified record skeleton."""
    return {
        "format": FORMAT,
        "version": VERSION,
        "core_commit": "",
        "provider_commit": "",
        "sdk_version": _pinned_sdk(),
        "adapter_id": "",
        "sender_profile_tag": "",
        "recipient_profile_tag": "",
        "sender_worker": dict.fromkeys(sorted(SENDER_WORKER_KEYS)),
        "recipient_worker": dict.fromkeys(sorted(RECIPIENT_WORKER_KEYS)),
        "target_channel": "",
        "recipient_target_channel": "",
        "sibling_channels": [],
        "recipient_sibling_channels": [],
        "roster_member_sha256s": {},
        "servers_local_only": False,
        "provider_src_matches_pin": False,
        "harness_commit": "",
        "cases": {
            "group_send_observed": {
                "passed": False,
                "sent_text_sha256": "",
                "send_ns": None,
                "ledger": dict.fromkeys(sorted(LEDGER_KEYS)),
                "expected_attempt": dict.fromkeys(sorted(ATTEMPT_KEYS)),
                "observations": [],
                "sibling_observations": [],
            },
            "native_quote_target": {
                "passed": False,
                "sent_text_sha256": "",
                "send_ns": None,
                "ledger": dict.fromkeys(sorted(LEDGER_KEYS)),
                "expected_attempt": dict.fromkeys(sorted(ATTEMPT_KEYS)),
                "observations": [],
                "sibling_observations": [],
                "parent_item_id": None,
                "parent_text_sha256": "",
            },
            "restart_continuation": {
                "passed": False,
                "redrive_ns": None,
                "kill_ns": None,
                "reused_native_ref": "",
                "ledger": dict.fromkeys(sorted(LEDGER_KEYS)),
                "reply_text_sha256": "",
                "reply_observations_total": None,
                "reply_retained_after_redrive": False,
                "new_sends": None,
            },
            "transient_worker_admission": {
                "passed": False,
                "sent_text_sha256": "",
                "recipient_send_ns": None,
                "journal_entry": None,
            },
        },
    }


def _check_revisions(
    record: dict[str, Any],
    errors: list[str],
    *,
    expected_core_commit: str,
    expected_provider_commit: str,
) -> None:
    for expected in (expected_core_commit, expected_provider_commit):
        if (
            not isinstance(expected, str)
            or not COMMIT.fullmatch(expected)
            or expected == "0" * 40
        ):
            errors.append(
                "Expected revisions must be non-null immutable 40-character commits"
            )
            return
    if record.get("core_commit") != expected_core_commit:
        errors.append("Core revision does not match the selected qualification target")
    if record.get("provider_commit") != expected_provider_commit:
        errors.append(
            "Provider revision does not match the selected qualification target"
        )
    if record.get("sdk_version") != _pinned_sdk():
        errors.append("SDK version must match the pinned simplex-chat contract")
    if record.get("provider_src_matches_pin") is not True:
        errors.append("Provider production source must match the pinned revision")
    harness = record.get("harness_commit")
    if (
        not isinstance(harness, str)
        or not COMMIT.fullmatch(harness)
        or harness == "0" * 40
    ):
        errors.append("harness_commit must identify an immutable 40-character commit")


def _check_shape(record: Any, errors: list[str]) -> bool:
    if not isinstance(record, dict):
        errors.append("Evidence must be one object")
        return False
    if set(record) != TOP_KEYS:
        errors.append("Evidence must contain exactly the supported fields")
        return False
    if record.get("format") != FORMAT:
        errors.append("Unsupported evidence format")
    if type(record.get("version")) is not int or record["version"] != VERSION:
        errors.append("Unsupported evidence version")
    for key, expected in (
        ("sender_worker", SENDER_WORKER_KEYS),
        ("recipient_worker", RECIPIENT_WORKER_KEYS),
    ):
        worker = record.get(key)
        if not isinstance(worker, dict) or set(worker) != expected:
            errors.append(f"{key} lifecycle must contain exactly the supported fields")
            return False
        for name, value in worker.items():
            if name == "exit_signal":
                if value is not None and not isinstance(value, str):
                    errors.append(f"{key}.{name} must be a signal name or null")
                continue
            if name in {
                "exited_ns",
                "redrive_pid",
                "redrive_ready_ns",
                "redrive_started_ns",
            }:
                if value is not None and type(value) is not int:
                    errors.append(f"{key}.{name} must be an integer or null")
                continue
            if type(value) is not int or value <= 0:
                errors.append(f"{key}.{name} must be a positive integer")
        stop = worker.get("stopped_ns")
        ready = worker.get("ready_ns")
        if type(stop) is int and type(ready) is int and stop < ready:
            errors.append(f"{key} lifecycle order is inconsistent")
    sender = record.get("sender_profile_tag")
    recipient = record.get("recipient_profile_tag")
    if (
        not _printable_tag(sender, 128)
        or not _printable_tag(recipient, 128)
        or sender == recipient
    ):
        errors.append("Two distinct bounded profile tags are required")
    if not _printable_tag(record.get("adapter_id"), 64):
        errors.append("A bounded adapter identifier is required")
    if not CHANNEL.fullmatch(str(record.get("target_channel") or "")):
        errors.append("target_channel must be a group conversation channel")
    if not CHANNEL.fullmatch(str(record.get("recipient_target_channel") or "")):
        errors.append("recipient_target_channel must be a group conversation channel")
    for key in ("sibling_channels", "recipient_sibling_channels"):
        channels = record.get(key)
        if (
            not isinstance(channels, list)
            or not channels
            or any(not CHANNEL.fullmatch(str(channel)) for channel in channels)
        ):
            errors.append(f"{key} must list at least one sibling group channel")
        elif (
            record.get(
                "target_channel"
                if key == "sibling_channels"
                else "recipient_target_channel"
            )
            in channels
        ):
            errors.append(f"{key} must not include its profile target channel")
    roster = record.get("roster_member_sha256s")
    if not isinstance(roster, dict) or not roster:
        errors.append("A recipient-side member roster is required")
        return False
    else:
        for channel, members in roster.items():
            if not CHANNEL.fullmatch(str(channel)):
                errors.append("Roster channels must be group conversation channels")
            if (
                not isinstance(members, list)
                or len(members) < 2
                or any(
                    not isinstance(member, str) or not SHA256.fullmatch(member)
                    for member in members
                )
            ):
                errors.append("Each roster channel needs at least two member digests")
                return False
            if len(set(members)) != len(members):
                errors.append("Recipient rosters must contain distinct member digests")
                return False
    if record.get("servers_local_only") is not True:
        errors.append("Both profiles must be restricted to the local server only")
    return True


def _check_observation(
    observation: Any, errors: list[str], label: str
) -> dict[str, Any] | None:
    if not isinstance(observation, dict) or set(observation) != OBSERVATION_KEYS:
        errors.append(f"{label}: unexpected observation fields")
        return None
    if not CHANNEL.fullmatch(str(observation.get("channel") or "")):
        errors.append(f"{label}: observation channel must be a group channel")
    if type(observation.get("item_id")) is not int or observation["item_id"] <= 0:
        errors.append(f"{label}: positive native item id required")
    if not isinstance(observation.get("text_sha256"), str) or not SHA256.fullmatch(
        observation["text_sha256"]
    ):
        errors.append(f"{label}: observed text digest required")
    member = observation.get("sender_member_sha256")
    if not isinstance(member, str) or not SHA256.fullmatch(member):
        errors.append(f"{label}: sender member digest required")
    if type(observation.get("observed_ns")) is not int:
        errors.append(f"{label}: observation timestamp required")
    quote = observation.get("quoted_item_id")
    quoted = observation.get("quoted_text_sha256")
    if quote is not None and (type(quote) is not int or quote <= 0):
        errors.append(f"{label}: quote target must be a positive item id or absent")
    if quoted is not None and not (
        isinstance(quoted, str) and SHA256.fullmatch(quoted)
    ):
        errors.append(f"{label}: quoted text digest must be a digest or absent")
    if (quote is None) != (quoted is None):
        errors.append(f"{label}: quote target and quoted text must be paired")
    return observation


def _check_ledger(ledger: Any, errors: list[str], label: str) -> None:
    if not isinstance(ledger, dict) or set(ledger) != LEDGER_KEYS:
        errors.append(f"{label}: unexpected ledger fields")
        return
    if not isinstance(ledger.get("event_id"), str) or not ledger["event_id"]:
        errors.append(f"{label}: ledger event identity required")
    if not isinstance(ledger.get("outbox_id"), str) or not ledger["outbox_id"]:
        errors.append(f"{label}: ledger outbox identity required")
    if type(ledger.get("attempt_number")) is not int or ledger["attempt_number"] <= 0:
        errors.append(f"{label}: positive attempt number required")
    if not CHANNEL.fullmatch(str(ledger.get("channel") or "")):
        errors.append(f"{label}: ledger channel must be a group channel")
    if ledger.get("accepted") is not True:
        errors.append(f"{label}: the handoff ledger must record an accepted fact")
    native_ref = ledger.get("native_ref")
    item_id = ledger.get("native_item_id")
    if not isinstance(native_ref, str) or not native_ref:
        errors.append(f"{label}: accepted native reference required")
    if type(item_id) is not int or item_id <= 0:
        errors.append(f"{label}: accepted native item id required")
    if isinstance(native_ref, str) and native_ref:
        match = re.search(r":group:([1-9][0-9]*):item:([1-9][0-9]*)$", native_ref)
        if (
            match is None
            or f"group:{match.group(1)}" != ledger.get("channel")
            or type(item_id) is not int
            or int(match.group(2)) != item_id
        ):
            errors.append(
                f"{label}: native reference and channel/item identity disagree"
            )


def _check_attempt(expected: Any, ledger: Any, errors: list[str], label: str) -> None:
    if not isinstance(expected, dict) or set(expected) != ATTEMPT_KEYS:
        errors.append(f"{label}: independent attempt identity required")
        return
    if (
        not isinstance(expected.get("event_id"), str)
        or not expected["event_id"]
        or not isinstance(expected.get("outbox_id"), str)
        or not expected["outbox_id"]
        or type(expected.get("attempt_number")) is not int
        or expected["attempt_number"] <= 0
    ):
        errors.append(f"{label}: invalid independent attempt identity")
    if isinstance(ledger, dict) and any(
        ledger.get(key) != expected[key] for key in ATTEMPT_KEYS
    ):
        errors.append(f"{label}: ledger differs from independently attempted identity")


def _check_case_bindings(record: dict[str, Any], errors: list[str]) -> None:
    cases = record.get("cases")
    if not isinstance(cases, dict) or set(cases) != set(CASES):
        errors.append("All four required two-profile cases must be present")
        return
    if any(not isinstance(cases[name], dict) for name in CASES):
        errors.append("All required cases must contain evidence objects")
        return
    recipient = record.get("recipient_worker") or {}
    window_start = recipient.get("ready_ns")
    window_stop = recipient.get("stopped_ns")
    if type(window_start) is not int or type(window_stop) is not int:
        errors.append("Recipient observer lifecycle timestamps are required")
    known_observations: dict[tuple[str, int], str] = {}
    target = record.get("target_channel")
    recipient_target = record.get("recipient_target_channel")
    roster = record.get("roster_member_sha256s") or {}
    if not isinstance(roster, dict):
        errors.append("Recipient roster must be an object")
        return
    if recipient_target not in roster:
        errors.append("Recipient target roster is missing")
    parent_observation: dict[str, Any] | None = None
    sender_worker = record.get("sender_worker") or {}
    kill: Any = None
    redrive: Any = None

    def covered(stamp: Any, label: str) -> None:
        if type(stamp) is not int:
            errors.append(f"{label}: a monotonic timestamp is required")
            return
        if type(window_start) is int and stamp < window_start:
            errors.append(f"{label}: before the recipient observer was ready")
        if type(window_stop) is int and stamp > window_stop:
            errors.append(f"{label}: after the recipient observer stopped")

    def collect(observations: Any, label: str) -> list[dict[str, Any]]:
        checked: list[dict[str, Any]] = []
        if not isinstance(observations, list):
            errors.append(f"{label}: observations must be a list")
            return checked
        for index, observation in enumerate(observations):
            parsed = _check_observation(observation, errors, f"{label}[{index}]")
            if parsed is None:
                continue
            channel = parsed.get("channel")
            item_id = parsed.get("item_id")
            if type(channel) is str and type(item_id) is int:
                identity = (channel, item_id)
                if identity in known_observations:
                    errors.append(
                        f"{label}[{index}]: duplicate observation of one native item"
                    )
                known_observations[identity] = label
            covered(parsed.get("observed_ns"), f"{label}[{index}]")
            member = parsed.get("sender_member_sha256")
            members = roster.get(channel) if type(channel) is str else None
            if not isinstance(members, list) or len(members) < 2:
                errors.append(f"{label}[{index}]: destination roster is missing")
            elif member not in members:
                errors.append(
                    f"{label}[{index}]: observed by a peer outside the recipient roster"
                )
            checked.append(parsed)
        return checked

    def refuse_sibling(observations: Any, label: str) -> None:
        if not isinstance(observations, list):
            errors.append(f"{label}: sibling observations must be a list")
            return
        for index, observation in enumerate(observations):
            if _check_observation(observation, errors, f"{label}[{index}]") is not None:
                errors.append(f"{label}: a sibling destination received the message")

    group = cases.get("group_send_observed")
    if isinstance(group, dict):
        if set(group) != CASE_KEYS["group_send_observed"]:
            errors.append("group_send_observed: unexpected case fields")
        if group.get("passed") is not True:
            errors.append("group_send_observed: recipient-side pass not recorded")
        if not isinstance(group.get("sent_text_sha256"), str) or not SHA256.fullmatch(
            group.get("sent_text_sha256") or ""
        ):
            errors.append("group_send_observed: sent text digest required")
        covered(group.get("send_ns"), "group_send_observed send")
        if (
            type(group.get("send_ns")) is int
            and type(sender_worker.get("ready_ns")) is int
            and group["send_ns"] < sender_worker["ready_ns"]
        ):
            errors.append("group_send_observed: send predates sender readiness")
        _check_ledger(group.get("ledger"), errors, "group_send_observed ledger")
        _check_attempt(
            group.get("expected_attempt"),
            group.get("ledger"),
            errors,
            "group_send_observed",
        )
        ledger = group.get("ledger")
        if isinstance(ledger, dict) and ledger.get("channel") != target:
            errors.append(
                "group_send_observed: ledger identity disagrees with the target"
            )
        observed = collect(group.get("observations"), "group_send_observed")
        if len(observed) != 1:
            errors.append(
                "group_send_observed: exactly one recipient observation required"
            )
        else:
            parent_observation = observed[0]
            if observed[0].get("channel") != recipient_target:
                errors.append(
                    "group_send_observed: the observation is not on the target channel"
                )
            if (
                type(group.get("send_ns")) is int
                and type(observed[0].get("observed_ns")) is int
                and observed[0]["observed_ns"] < group["send_ns"]
            ):
                errors.append("group_send_observed: observation predates send")
            if observed[0].get("text_sha256") != group.get("sent_text_sha256"):
                errors.append(
                    "group_send_observed: the observed plaintext differs from the send"
                )
        refuse_sibling(group.get("sibling_observations"), "group_send_observed sibling")

    quote = cases.get("native_quote_target")
    if isinstance(quote, dict):
        if set(quote) != CASE_KEYS["native_quote_target"]:
            errors.append("native_quote_target: unexpected case fields")
        if quote.get("passed") is not True:
            errors.append("native_quote_target: recipient-side pass not recorded")
        if not isinstance(quote.get("sent_text_sha256"), str) or not SHA256.fullmatch(
            quote.get("sent_text_sha256") or ""
        ):
            errors.append("native_quote_target: sent reply digest required")
        if type(quote.get("parent_item_id")) is not int or quote["parent_item_id"] <= 0:
            errors.append("native_quote_target: recipient parent item id required")
        if not isinstance(quote.get("parent_text_sha256"), str) or not SHA256.fullmatch(
            quote.get("parent_text_sha256") or ""
        ):
            errors.append("native_quote_target: parent text digest required")
        covered(quote.get("send_ns"), "native_quote_target send")
        _check_ledger(quote.get("ledger"), errors, "native_quote_target ledger")
        _check_attempt(
            quote.get("expected_attempt"),
            quote.get("ledger"),
            errors,
            "native_quote_target",
        )
        ledger = quote.get("ledger")
        if isinstance(ledger, dict) and ledger.get("channel") != target:
            errors.append(
                "native_quote_target: ledger identity disagrees with the target"
            )
        observed = collect(quote.get("observations"), "native_quote_target")
        if len(observed) != 1:
            errors.append(
                "native_quote_target: exactly one recipient observation required"
            )
        elif parent_observation is None:
            errors.append("native_quote_target: the quoted parent was not bound")
        else:
            reply = observed[0]
            if quote.get("parent_item_id") != parent_observation.get("item_id"):
                errors.append(
                    "native_quote_target: declared parent differs from observation"
                )
            if quote.get("parent_text_sha256") != parent_observation.get("text_sha256"):
                errors.append(
                    "native_quote_target: declared parent digest differs from observation"
                )
            if (
                type(quote.get("send_ns")) is int
                and type(reply.get("observed_ns")) is int
                and reply["observed_ns"] < quote["send_ns"]
            ):
                errors.append("native_quote_target: observation predates send")
            if (
                type(quote.get("send_ns")) is int
                and type(parent_observation.get("observed_ns")) is int
                and quote["send_ns"] < parent_observation["observed_ns"]
            ):
                errors.append(
                    "native_quote_target: reply send predates parent observation"
                )
            if reply.get("channel") != recipient_target:
                errors.append("native_quote_target: reply is not on the target channel")
            if reply.get("text_sha256") != quote.get("sent_text_sha256"):
                errors.append(
                    "native_quote_target: reply plaintext differs from the send"
                )
            if reply.get("quoted_item_id") is None:
                errors.append(
                    "native_quote_target: the observed reply quote is missing"
                )
            elif reply.get("quoted_item_id") != quote.get("parent_item_id"):
                errors.append("native_quote_target: the quote targets another message")
            if reply.get("quoted_text_sha256") != quote.get("parent_text_sha256"):
                errors.append(
                    "native_quote_target: the quoted text differs from the parent"
                )
        refuse_sibling(quote.get("sibling_observations"), "native_quote_target sibling")

    restart = cases.get("restart_continuation")
    sender = record.get("sender_worker") or {}
    if isinstance(restart, dict):
        if set(restart) != CASE_KEYS["restart_continuation"]:
            errors.append("restart_continuation: unexpected case fields")
        if restart.get("passed") is not True:
            errors.append("restart_continuation: recipient-side pass not recorded")
        if not isinstance(
            restart.get("reply_text_sha256"), str
        ) or not SHA256.fullmatch(restart.get("reply_text_sha256") or ""):
            errors.append("restart_continuation: reply digest required")
        kill = restart.get("kill_ns")
        redrive = restart.get("redrive_ns")
        covered(redrive, "restart_continuation redrive")
        if type(kill) is not int:
            errors.append("restart_continuation: kill timestamp required")
        elif (
            isinstance(quote, dict)
            and isinstance(quote.get("observations"), list)
            and len(quote["observations"]) == 1
            and isinstance(quote["observations"][0], dict)
            and type(quote["observations"][0].get("observed_ns")) is int
            and kill < quote["observations"][0]["observed_ns"]
        ):
            errors.append("restart_continuation: killed before reply observation")
        if type(redrive) is not int or (type(kill) is int and redrive <= kill):
            errors.append(
                "restart_continuation: the redrive must follow the worker exit"
            )
        exited = sender.get("exited_ns")
        if sender.get("exit_signal") != "SIGKILL" or type(exited) is not int:
            errors.append("restart_continuation: confirmed SIGKILL exit required")
        if type(kill) is int and type(exited) is int and exited < kill:
            errors.append(
                "restart_continuation: the exit predates the injected failure"
            )
        if recipient.get("pid") in {
            sender.get("pid"),
            sender.get("redrive_pid"),
        } or sender.get("pid") == sender.get("redrive_pid"):
            errors.append(
                "restart_continuation: independent worker process identities required"
            )
        started = sender.get("redrive_started_ns")
        ready = sender.get("redrive_ready_ns")
        if type(started) is not int or type(exited) is not int or started <= exited:
            errors.append(
                "restart_continuation: replacement must start after sender exit"
            )
        if type(ready) is not int or type(started) is not int or ready < started:
            errors.append(
                "restart_continuation: replacement readiness must follow launch"
            )
        if type(redrive) is int and type(ready) is int and redrive < ready:
            errors.append(
                "restart_continuation: redrive must follow replacement readiness"
            )
        redrive_pid = sender.get("redrive_pid")
        if (
            type(redrive_pid) is not int
            or redrive_pid <= 0
            or redrive_pid == sender.get("pid")
        ):
            errors.append("restart_continuation: a fresh worker process must redrive")
        if type(sender.get("redrive_ready_ns")) is not int:
            errors.append("restart_continuation: redrive readiness required")
        reused = restart.get("reused_native_ref")
        if not isinstance(reused, str) or not reused:
            errors.append("restart_continuation: reused native reference required")
        ledger = restart.get("ledger")
        if not isinstance(ledger, dict):
            errors.append("restart_continuation: accepted reply ledger is required")
        if isinstance(ledger, dict):
            _check_ledger(ledger, errors, "restart_continuation ledger")
            reply_ledger = quote.get("ledger") if isinstance(quote, dict) else None
            if not isinstance(reply_ledger, dict) or any(
                ledger.get(key) != reply_ledger.get(key) for key in LEDGER_KEYS
            ):
                errors.append(
                    "restart_continuation: ledger differs from original reply handoff"
                )
            if (
                isinstance(reused, str)
                and reused
                and ledger.get("native_ref") != reused
            ):
                errors.append(
                    "restart_continuation: the ledger reference was not reused"
                )
        if not isinstance(quote, dict) or restart.get("reply_text_sha256") != quote.get(
            "sent_text_sha256"
        ):
            errors.append(
                "restart_continuation: reply digest differs from original reply"
            )
        if restart.get("reply_retained_after_redrive") is not True:
            errors.append(
                "restart_continuation: retained reply not reobserved after redrive"
            )
        total = restart.get("reply_observations_total")
        if type(total) is not int or total != 1:
            errors.append(
                "restart_continuation: exactly one reply copy may exist after redrive"
            )
        if restart.get("new_sends") != 0:
            errors.append("restart_continuation: the redrive must not submit new sends")

    transient = cases.get("transient_worker_admission")
    if isinstance(transient, dict):
        if set(transient) != CASE_KEYS["transient_worker_admission"]:
            errors.append("transient_worker_admission: unexpected case fields")
        if transient.get("passed") is not True:
            errors.append("transient_worker_admission: pass not recorded")
        if not isinstance(
            transient.get("sent_text_sha256"), str
        ) or not SHA256.fullmatch(transient.get("sent_text_sha256") or ""):
            errors.append("transient_worker_admission: sent digest required")
        if type(kill) is not int:
            errors.append("transient_worker_admission: kill timestamp required")
        else:
            sent = transient.get("recipient_send_ns")
            if type(sent) is not int or not (
                type(exited) is int and type(started) is int and exited < sent < started
            ):
                errors.append(
                    "transient_worker_admission: the message must arrive while the "
                    "sender worker is down"
                )
            else:
                covered(sent, "transient_worker_admission recipient send")
        entry = transient.get("journal_entry")
        if not isinstance(entry, dict) or not entry:
            errors.append(
                "transient_worker_admission: durable admission after restart is required"
            )
        elif set(entry) != {
            "event_id",
            "text_sha256",
            "provenance",
            "channel",
            "event_kind",
            "admitted_ns",
        }:
            errors.append("transient_worker_admission: unexpected journal fields")
        else:
            if entry.get("text_sha256") != transient.get("sent_text_sha256"):
                errors.append("transient_worker_admission: admitted plaintext differs")
            if not isinstance(entry.get("provenance"), str) or entry.get(
                "provenance"
            ) not in {"recovered", "live"}:
                errors.append(
                    "transient_worker_admission: unexpected admission provenance"
                )
            if entry.get("channel") != target:
                errors.append(
                    "transient_worker_admission: admission on another channel"
                )
            if entry.get("event_kind") != "message.created":
                errors.append("transient_worker_admission: creation event required")
            if not isinstance(entry.get("event_id"), str) or not entry["event_id"]:
                errors.append(
                    "transient_worker_admission: admitted event identity required"
                )
            if type(entry.get("admitted_ns")) is not int:
                errors.append(
                    "transient_worker_admission: admission timestamp required"
                )
            elif (
                type(transient.get("recipient_send_ns")) is int
                and type(started) is int
                and entry["admitted_ns"] < max(transient["recipient_send_ns"], started)
            ):
                errors.append(
                    "transient_worker_admission: admission predates send or replacement"
                )


def evaluate(
    record: Any, *, expected_core_commit: str, expected_provider_commit: str
) -> tuple[str, ...]:
    """Refuse incomplete or self-contradicting records for the selected revisions."""
    errors: list[str] = []
    if not _check_shape(record, errors):
        return tuple(errors)
    _check_revisions(
        record,
        errors,
        expected_core_commit=expected_core_commit,
        expected_provider_commit=expected_provider_commit,
    )
    _check_case_bindings(record, errors)
    return tuple(errors)


async def resolved_commit(tree: Path) -> str:
    """Return the immutable HEAD commit of a pinned source checkout."""
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(tree),
        "rev-parse",
        "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(30):
            stdout, _ = await process.communicate()
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    revision = stdout.decode().strip()
    if process.returncode != 0 or not COMMIT.fullmatch(revision):
        raise RuntimeError(f"No immutable revision available for {tree}")
    return revision


async def _source_unchanged(tree: Path, pinned: str, source: str) -> bool:
    """Compare imported bytes, including index edits and untracked importable files.

    A commit-to-commit comparison alone ignores edits in the working directory.
    """

    async def git(*args: str) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(tree),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(30):
                output, _ = await process.communicate()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return process.returncode or 0, output.decode().strip()

    status, _ = await git("diff", "--quiet", pinned, "--", source)
    if status != 0:
        return False
    status, untracked = await git(
        "ls-files", "--others", "--exclude-standard", "--", source
    )
    return status == 0 and not untracked


async def _provider_src_unchanged(tree: Path, pinned: str) -> bool:
    return await _source_unchanged(tree, pinned, "src")


def _validate_local_server(server: str) -> None:
    """This one-host tier permits only numeric loopback SMP endpoints."""
    if not isinstance(server, str) or any(ch.isspace() for ch in server):
        raise ValueError("The SMP server must use a numeric loopback address")
    try:
        parsed = urlsplit(server)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The SMP server must use a numeric loopback address") from exc
    if (
        parsed.scheme != "smp"
        or not address.is_loopback
        or not parsed.username
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("The SMP server must use a numeric loopback address")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _native_view(record: Any) -> dict[str, Any] | None:
    """Extract the bounded observation view from one raw native chat item."""
    info = (record or {}).get("chatInfo") or {}
    item = (record or {}).get("chatItem") or {}
    content = item.get("content") or {}
    if (
        info.get("type") != "group"
        or content.get("type") != "rcvMsgContent"
        or (content.get("msgContent") or {}).get("type") != "text"
    ):
        return None
    number = (info.get("groupInfo") or {}).get("groupId")
    meta = item.get("meta") or {}
    item_id = meta.get("itemId")
    text = content["msgContent"].get("text")
    direction = item.get("chatDir") or {}
    member = direction.get("groupMember") or {}
    member_id = member.get("memberId")
    quote = item.get("quotedItem") or {}
    quoted_id = quote.get("itemId")
    quoted_text = (quote.get("content") or {}).get("text")
    if (
        type(number) is not int
        or type(item_id) is not int
        or not isinstance(text, str)
        or direction.get("type") != "groupRcv"
        or not isinstance(member_id, str)
        or item.get("file") is not None
    ):
        return None
    return {
        "channel": f"group:{number}",
        "item_id": item_id,
        "text_sha256": _sha256(text),
        "sender_member_sha256": _sha256(member_id),
        "quoted_item_id": quoted_id if type(quoted_id) is int else None,
        "quoted_text_sha256": (
            _sha256(quoted_text) if isinstance(quoted_text, str) else None
        ),
        "observed_ns": time.monotonic_ns(),
    }


def _journal_entries(state_dir: Path) -> list[dict[str, Any]]:
    path = Path(state_dir) / "admission.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeError):
        return []
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


class DurableHarnessContext:
    """Durable admission, checkpoint and health callbacks for adapter start.

    Admitted events append to a JSONL journal with their ingress provenance;
    checkpoints persist in one JSON file shared across adapter restarts. This
    keeps the production adapter start path intact without a full core runtime
    and without in-memory doubles on the admission boundary.
    """

    def __init__(self, adapter_id: str, state_dir: Path) -> None:
        from medre.core.contracts.adapter import AdapterContext

        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "admission.jsonl"
        self.checkpoints_path = self.state_dir / "checkpoints.json"
        self._checkpoint_lock = asyncio.Lock()
        self.context = AdapterContext(
            adapter_id,
            self._publish,
            logging.getLogger("medre.two-profile-qualify"),
            self._clock,
            asyncio.Event(),
            admit_inbound=self._admit,
            load_checkpoint=self._load,
            commit_checkpoint=self._commit,
        )

    def _clock(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    async def _publish(self, event: Any, attachment: Any = None) -> None:
        raise RuntimeError("The harness requires durable admission")

    async def _admit(self, event: Any, provenance: Any, attachment: Any = None) -> None:
        payload = event.payload if hasattr(event, "payload") else {}
        body = payload.get("body") if isinstance(payload, dict) else None
        entry = {
            "event_id": event.event_id,
            "text_sha256": _sha256(body) if isinstance(body, str) and body else "",
            "provenance": provenance,
            "channel": event.source_channel_id,
            "event_kind": event.event_kind,
            "admitted_ns": time.monotonic_ns(),
        }
        await asyncio.to_thread(self._append_entry, entry)

    def _append_entry(self, entry: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    async def _load(self, stream: str) -> Any:
        try:
            text = await asyncio.to_thread(self.checkpoints_path.read_text, "utf-8")
            checkpoints = json.loads(text)
        except (FileNotFoundError, ValueError):
            return None
        stored = checkpoints.get(stream)
        if stored is None:
            return None
        return SimpleCheckpoint(stored["cursor"], stored["metadata_json"])

    def _commit_sync(self, stream: str, cursor: str, metadata_json: str) -> None:
        """Write both stream cursors together; replace crash-atomically."""
        try:
            checkpoints = json.loads(self.checkpoints_path.read_text("utf-8"))
        except FileNotFoundError:
            checkpoints = {}
        if not isinstance(checkpoints, dict):
            raise ValueError("Stored checkpoint map is invalid")
        checkpoints[stream] = {"cursor": cursor, "metadata_json": metadata_json}
        temp = self.checkpoints_path.with_suffix(".json.tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(checkpoints, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.checkpoints_path)
        finally:
            temp.unlink(missing_ok=True)

    async def _commit(self, stream: str, cursor: str, metadata_json: str) -> None:
        async with self._checkpoint_lock:
            await asyncio.to_thread(self._commit_sync, stream, cursor, metadata_json)


class SimpleCheckpoint:
    """Minimal load_checkpoint result shape the adapter consumes."""

    def __init__(self, cursor: str, metadata_json: str) -> None:
        self.cursor = cursor
        self.metadata_json = metadata_json


async def _provision_profile(
    prefix: Path, key: str, tag: str, server: str
) -> tuple[Any, int]:
    """Create one dedicated profile restricted to the operator's local server.

    Provisioning is an explicit campaign step per the hardware lab procedure;
    the production worker processes take the profiles over afterwards.
    """
    from simplex_chat import ChatApi, MigrationConfirmation, SqliteDb

    api = await ChatApi.init(
        SqliteDb(str(prefix), key), confirm=MigrationConfirmation.YES_UP
    )
    try:
        user = await api.api_create_active_user(
            {"displayName": tag, "fullName": "", "peerType": "bot"}
        )
        await api.start_chat()
        await api.send_chat_cmd(f"/smp {server}")
        state = await api.send_chat_cmd("/smp")
        enabled = [
            entry["server"]
            for operator in state["userServers"]
            for entry in operator["smpServers"]
            if entry.get("enabled")
        ]
        if enabled != [server]:
            raise RuntimeError("The profile must use exactly the local server")
        return api, int(user["userId"])
    except BaseException:
        await api.close()
        raise


def _full_link(record: Any) -> str:
    """Require the server-embedded full connection link, never a hosted shortcut."""
    connection = (record or {}).get("connLinkContact") or {}
    link = connection.get("connFullLink")
    if not isinstance(link, str) or not link.startswith("simplex:/"):
        raise RuntimeError("A server-embedded full group link is required")
    return link


def _own_member_digest(group: Any) -> str | None:
    membership = (group or {}).get("membership") or {}
    member_id = membership.get("memberId")
    return _sha256(member_id) if isinstance(member_id, str) and member_id else None


async def _join_group(
    api: Any,
    user_id: int,
    link: str,
    known: set[int] | None = None,
    deadline_s: float = 60.0,
) -> int:
    """Join one group through a full link and wait for a connected membership.

    ``known`` excludes groups already joined through earlier links, so each
    call returns a distinct native group identity.
    """
    already = known or set()
    await api.api_connect_active_user(link)
    wake = asyncio.Event()
    async with asyncio.timeout(deadline_s):
        while True:
            for group in await api.api_list_groups(user_id):
                membership = group.get("membership") or {}
                number = group.get("groupId")
                if (
                    membership.get("memberStatus") in {"connected", "complete"}
                    and type(number) is int
                    and number not in already
                ):
                    return number
            try:
                await asyncio.wait_for(wake.wait(), timeout=0.1)
            except TimeoutError:
                pass
    raise RuntimeError("Unreachable")


async def _ensure_group_link(api: Any, group_id: int) -> str:
    from simplex_chat.core import ChatAPIError

    try:
        record = await api.api_get_group_link(group_id)
    except ChatAPIError as exc:
        if exc.store_error_type != "groupLinkNotFound":
            raise
        await api.api_create_group_link(group_id, "member")
        record = await api.api_get_group_link(group_id)
    return _full_link(record)


def _roster(
    recipient_groups: Any, sender_digests: dict[str, str | None]
) -> dict[str, list[str]]:
    """Digest per-group member rosters captured during provisioning.

    Each roster holds the recipient's own membership identity and the sender's
    provisioning-time identity for that group. It is captured before any
    campaign observation exists, so the unknown-peer refusal cannot be
    satisfied by the observations it checks.
    """
    roster: dict[str, list[str]] = {}
    for group in recipient_groups or ():
        number = (group or {}).get("groupId")
        if type(number) is not int:
            continue
        channel = f"group:{number}"
        own = _own_member_digest(group)
        members = {digest for digest in (own, sender_digests.get(channel)) if digest}
        if len(members) >= 2:
            roster[channel] = sorted(members)
    return roster


async def _redrive_with_send_count(adapter: Any, rendering: Any) -> tuple[Any, int]:
    """Count native worker send requests, even when the request fails."""
    session = adapter.session
    native_call = session.call
    requests = 0

    async def counted(operation: str, **args: Any) -> Any:
        nonlocal requests
        if operation == "send":
            requests += 1
        return await native_call(operation, **args)

    session.call = counted
    try:
        result = await adapter.deliver(rendering)
    finally:
        session.call = native_call
    return result, requests


def _fresh_reply_retained(
    native_items: Any, *, channel: str, reply: dict[str, Any], parent: dict[str, Any]
) -> bool:
    """Reinspect the exact native item, not a prior cached observation."""
    if not isinstance(native_items, list):
        return False
    for item in native_items:
        view = _native_view(item)
        if view is not None and all(
            view.get(key) == expected
            for key, expected in (
                ("channel", channel),
                ("item_id", reply.get("item_id")),
                ("text_sha256", reply.get("text_sha256")),
                ("quoted_item_id", parent.get("item_id")),
                ("quoted_text_sha256", parent.get("text_sha256")),
            )
        ):
            return True
    return False


def _rendered(
    event_id: str,
    adapter_id: str,
    channel: str,
    chat_id: int,
    text: str,
    provenance: Any,
    reply_native_ref: str | None = None,
) -> Any:
    from medre.core.rendering.renderer import RenderingResult

    payload: dict[str, Any] = {"text": text, "chat_type": "group", "chat_id": chat_id}
    if reply_native_ref is not None:
        payload["reply_native_ref"] = reply_native_ref
        payload["reply_fallback_text"] = f"[reply fallback {event_id}]"
    return RenderingResult(
        event_id=event_id,
        target_adapter=adapter_id,
        target_channel=channel,
        payload=payload,
        attempt_provenance=provenance,
    )


def _ledger_row(state_dir: Path, outbox_id: str, attempt: int) -> dict[str, Any]:
    """Reread persisted handoff state with the ledger's own provenance key."""
    db = sqlite3.connect(str(Path(state_dir) / "handoffs.sqlite"))
    try:
        row = db.execute(
            "SELECT request,native_id FROM handoffs WHERE outbox=? AND attempt=?",
            (outbox_id, attempt),
        ).fetchone()
    finally:
        db.close()
    if row is None:
        return {}
    request = json.loads(row[0])
    provenance_fields = request[0]
    native_id = row[1]
    return {
        "event_id": provenance_fields["event_id"],
        "outbox_id": outbox_id,
        "attempt_number": attempt,
        "channel": request[1],
        "native_ref": native_id or "",
        "native_item_id": int(native_id.rpartition(":item:")[2]) if native_id else 0,
        "accepted": native_id is not None,
    }


class _Observer:
    """Independent recipient observation through its own worker process."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.observations: list[dict[str, Any]] = []
        self.seen: set[tuple[str, int]] = set()

    async def pump(self) -> None:
        pending = await self.session.call("events")
        for record in pending if isinstance(pending, list) else []:
            view = _native_view(record)
            if view is not None and (view["channel"], view["item_id"]) not in self.seen:
                self.seen.add((view["channel"], view["item_id"]))
                self.observations.append(view)

    async def history(self, channels: list[str]) -> None:
        for channel in channels:
            kind, number = channel.split(":", 1)
            response = await self.session.call(
                "history", chat_type=kind, chat_id=int(number), before=None
            )
            rows = response.get("chatItems") if isinstance(response, dict) else []
            for row in rows or []:
                view = _native_view(row)
                if (
                    view is not None
                    and (view["channel"], view["item_id"]) not in self.seen
                ):
                    self.seen.add((view["channel"], view["item_id"]))
                    self.observations.append(view)

    def matching(self, digest: str) -> list[dict[str, Any]]:
        return [o for o in self.observations if o["text_sha256"] == digest]

    def sibling_matches(self, digest: str, siblings: list[str]) -> list[dict[str, Any]]:
        return [
            o
            for o in self.observations
            if o["text_sha256"] == digest and o["channel"] in siblings
        ]

    def quoted_reply(
        self, digest: str, parent_item_id: Any, parent_digest: str
    ) -> bool:
        for view in self.matching(digest):
            if (
                view.get("quoted_item_id") == parent_item_id
                and view.get("quoted_text_sha256") == parent_digest
            ):
                return True
        return False

    async def wait_for(self, predicate: Any, timeout_s: float) -> None:
        """Wait on the worker wake signal, then poll native state until ready."""
        async with asyncio.timeout(timeout_s):
            while not predicate():
                try:
                    await asyncio.wait_for(self.session.wake.wait(), timeout=0.5)
                except TimeoutError:
                    pass
                await self.pump()
                if not predicate():
                    await self.history(
                        [self.session.config.chats[0], *self.session.config.chats[1:]]
                    )

    async def observe_window(self, seconds: float, channels: list[str]) -> None:
        """Poll live events and retained history for one bounded window."""
        deadline = time.monotonic_ns() + int(seconds * 1_000_000_000)
        while time.monotonic_ns() < deadline:
            try:
                await asyncio.wait_for(self.session.wake.wait(), timeout=0.5)
            except TimeoutError:
                pass
            await self.pump()
            await self.history(channels)


async def _wait_until(predicate: Any, timeout_s: float) -> None:
    """Wait on an internal deadline event until the predicate observes state."""
    wake = asyncio.Event()
    async with asyncio.timeout(timeout_s):
        while not predicate():
            try:
                await asyncio.wait_for(wake.wait(), timeout=0.05)
            except TimeoutError:
                pass


async def run_campaign(
    *,
    server: str,
    root: Path,
    expected_core_commit: str,
    expected_provider_commit: str,
) -> tuple[dict[str, Any], list[str]]:
    """Run the four two-profile cases once and return the content-free record.

    Returns the record and hard harness failures (empty on success). All
    monotonic timestamps share the one host clock domain.
    """
    from medre.core.events.delivery import DeliveryAttemptProvenance

    from medre.adapters.simplexchat.adapter import SimplexChatAdapter
    from medre.adapters.simplexchat.config import SimplexChatConfig
    from medre.adapters.simplexchat.session import SimplexChatSession

    _validate_local_server(server)
    run_id = secrets.token_hex(6)
    root = Path(root)
    await asyncio.to_thread(root.mkdir, 0o700, True, True)
    failures: list[str] = []

    sdk = check_installed_sdk()
    core_init = importlib.import_module("medre.core").__file__
    if not core_init:
        raise RuntimeError(
            "The core source tree is unavailable; an editable pinned core is required"
        )
    core_tree = (await asyncio.to_thread(Path(core_init).resolve)).parents[3]
    core_commit = await resolved_commit(core_tree)
    if core_commit != expected_core_commit or not await _source_unchanged(
        core_tree, expected_core_commit, "src"
    ):
        raise RuntimeError(
            "The installed core source does not match the selected qualification target"
        )
    adapter_tree = (
        await asyncio.to_thread(
            Path(importlib.import_module("medre.adapters.simplexchat").__file__).resolve
        )
    ).parents[4]
    provider_pin = expected_provider_commit
    if not COMMIT.fullmatch(provider_pin):
        raise RuntimeError(
            "The provider checkout does not match the selected qualification target"
        )
    provider_src_matches_pin = await _provider_src_unchanged(adapter_tree, provider_pin)
    if not provider_src_matches_pin:
        raise RuntimeError(
            "Provider production source differs from the pinned qualification revision"
        )
    harness_tree = (await asyncio.to_thread(Path(__file__).resolve)).parents[1]
    harness_commit = await resolved_commit(harness_tree)
    if not await _source_unchanged(
        harness_tree, harness_commit, "scripts/qualify_two_profile_recipient.py"
    ):
        raise RuntimeError("The executed qualification harness has uncommitted changes")

    sender_tag = f"sender-{secrets.token_hex(3)}"
    recipient_tag = f"recipient-{secrets.token_hex(3)}"
    profiles = root / "profiles"
    state_root = root / "state"
    key_sender = secrets.token_hex(24)
    key_recipient = secrets.token_hex(24)

    sender_digests: dict[str, str] = {}
    async with AsyncExitStack() as provision_cleanup:
        sender_api, sender_user = await _provision_profile(
            profiles / "sender" / "profile", key_sender, sender_tag, server
        )
        provision_cleanup.push_async_callback(sender_api.close)
        recipient_api, recipient_user = await _provision_profile(
            profiles / "recipient" / "profile", key_recipient, recipient_tag, server
        )
        provision_cleanup.push_async_callback(recipient_api.close)
        target_group = await sender_api.api_new_group(
            sender_user, {"displayName": f"camp-{run_id}", "fullName": ""}
        )
        sibling_group = await sender_api.api_new_group(
            sender_user, {"displayName": f"camp-{run_id}-sib", "fullName": ""}
        )
        target_sender = int(target_group["groupId"])
        sibling_sender = int(sibling_group["groupId"])
        target_link = await _ensure_group_link(sender_api, target_sender)
        sibling_link = await _ensure_group_link(sender_api, sibling_sender)
        sender_groups = await sender_api.api_list_groups(sender_user)
        for group in sender_groups:
            number = group.get("groupId")
            digest = _own_member_digest(group)
            if type(number) is int and digest:
                sender_digests[f"group:{number}"] = digest
        target_recipient = await _join_group(recipient_api, recipient_user, target_link)
        sibling_recipient = await _join_group(
            recipient_api, recipient_user, sibling_link, known={target_recipient}
        )

    target_channel = f"group:{target_sender}"
    recipient_target_channel = f"group:{target_recipient}"
    sibling_channels = [f"group:{sibling_sender}"]
    recipient_sibling_channels = [f"group:{sibling_recipient}"]
    recipient_sender_digests = {
        recipient_target_channel: sender_digests.get(target_channel),
        recipient_sibling_channels[0]: sender_digests.get(sibling_channels[0]),
    }

    def sender_config() -> SimplexChatConfig:
        return SimplexChatConfig(
            "qualifytwo",
            database_prefix=str(profiles / "sender" / "profile"),
            database_key=key_sender,
            user_id=sender_user,
            chats=(target_channel, *sibling_channels),
            state_dir=str(state_root / "sender"),
            poll_interval_seconds=0.05,
        )

    recipient_config = SimplexChatConfig(
        "qualifytwo",
        database_prefix=str(profiles / "recipient" / "profile"),
        database_key=key_recipient,
        user_id=recipient_user,
        chats=(recipient_target_channel, *recipient_sibling_channels),
        state_dir=str(state_root / "recipient"),
        poll_interval_seconds=0.05,
    )

    recipient_session: Any = None
    sender_adapter: Any = None
    redrive_adapter: Any = None
    observer: _Observer | None = None
    sender = {
        "pid": None,
        "ready_ns": None,
        "exited_ns": None,
        "exit_signal": None,
        "redrive_pid": None,
        "redrive_ready_ns": None,
        "redrive_started_ns": None,
        "stopped_ns": None,
    }
    recipient_lifecycle = {"pid": None, "ready_ns": None, "stopped_ns": None}
    nonce_parent = f"nonce=t2p-{run_id} parent body"
    nonce_reply = f"nonce=t2p-{run_id} reply body"
    nonce_transient = f"nonce=t2p-{run_id} transient body"
    parent_digest = _sha256(nonce_parent)
    reply_digest = _sha256(nonce_reply)
    transient_digest = _sha256(nonce_transient)
    parent_attempt = DeliveryAttemptProvenance(
        f"event-{run_id}-p",
        "plan",
        "qualifytwo",
        target_channel,
        f"outbox-{run_id}-p",
        1,
        "live",
    )
    reply_attempt = DeliveryAttemptProvenance(
        f"event-{run_id}-r",
        "plan",
        "qualifytwo",
        target_channel,
        f"outbox-{run_id}-r",
        1,
        "live",
    )
    record: dict[str, Any] | None = None
    try:
        recipient_session = SimplexChatSession(recipient_config)
        await recipient_session.start()
        recipient_lifecycle["pid"] = recipient_session.process.pid
        await recipient_session.call("start")
        recipient_lifecycle["ready_ns"] = time.monotonic_ns()
        observer = _Observer(recipient_session)
        recipient_groups = await recipient_session.call("groups")
        roster = _roster(recipient_groups, recipient_sender_digests)
        if recipient_target_channel not in roster:
            raise RuntimeError("Recipient roster for the target group is unavailable")

        sender_adapter = SimplexChatAdapter(sender_config())
        await sender_adapter.start(
            DurableHarnessContext("qualifytwo", state_root / "sender").context
        )
        sender["pid"] = sender_adapter.session.process.pid
        sender["ready_ns"] = time.monotonic_ns()

        # Case: routed group send observed by the independent recipient.
        parent_send_ns = time.monotonic_ns()
        parent_result = await sender_adapter.deliver(
            _rendered(
                parent_attempt.event_id,
                "qualifytwo",
                target_channel,
                target_sender,
                nonce_parent,
                parent_attempt,
            )
        )
        parent_ledger = _ledger_row(
            state_root / "sender",
            parent_attempt.outbox_id,
            parent_attempt.attempt_number,
        )
        await observer.wait_for(
            lambda: len(observer.matching(parent_digest)) == 1, timeout_s=60
        )
        parent_views = observer.matching(parent_digest)
        parent_view = parent_views[0]
        await observer.history(recipient_sibling_channels)
        sibling_hits = observer.sibling_matches(
            parent_digest, recipient_sibling_channels
        )

        # Case: native reply quoting the exact parent, observed by the recipient.
        reply_send_ns = time.monotonic_ns()
        reply_result = await sender_adapter.deliver(
            _rendered(
                reply_attempt.event_id,
                "qualifytwo",
                target_channel,
                target_sender,
                nonce_reply,
                reply_attempt,
                reply_native_ref=parent_result.native_message_id,
            )
        )
        reply_ledger = _ledger_row(
            state_root / "sender", reply_attempt.outbox_id, reply_attempt.attempt_number
        )
        await observer.wait_for(
            lambda: observer.quoted_reply(
                reply_digest, parent_view["item_id"], parent_digest
            ),
            timeout_s=60,
        )

        # Transient worker failure: kill the owned sender worker process group.
        os.killpg(sender["pid"], signal.SIGKILL)
        kill_ns = time.monotonic_ns()
        original_process = sender_adapter.session.process
        await sender_adapter.session.stop()
        if original_process is None or original_process.returncode != -signal.SIGKILL:
            raise RuntimeError("The sender worker was not reaped with SIGKILL")
        sender["exited_ns"] = time.monotonic_ns()
        sender["exit_signal"] = "SIGKILL"
        await sender_adapter.stop()
        sender_adapter = None

        # Admission preserved: the recipient sends while the sender worker is down.
        recipient_send_ns = time.monotonic_ns()
        await recipient_session.call(
            "send",
            chat_type="group",
            chat_id=target_recipient,
            text=nonce_transient,
            quoted_item_id=None,
        )

        # Fresh worker redrives on the same profile and admits the missed message.
        sender["redrive_started_ns"] = time.monotonic_ns()
        redrive_adapter = SimplexChatAdapter(sender_config())
        await redrive_adapter.start(
            DurableHarnessContext("qualifytwo", state_root / "sender").context
        )
        sender["redrive_pid"] = redrive_adapter.session.process.pid
        sender["redrive_ready_ns"] = time.monotonic_ns()

        def transient_admitted() -> bool:
            return any(
                entry.get("text_sha256") == transient_digest
                for entry in _journal_entries(state_root / "sender")
            )

        await _wait_until(transient_admitted, timeout_s=60)

        redrive_result, new_sends = await _redrive_with_send_count(
            redrive_adapter,
            _rendered(
                reply_attempt.event_id,
                "qualifytwo",
                target_channel,
                target_sender,
                nonce_reply,
                reply_attempt,
                reply_native_ref=parent_result.native_message_id,
            ),
        )
        redrive_ns = time.monotonic_ns()
        redrive_ledger = _ledger_row(
            state_root / "sender", reply_attempt.outbox_id, reply_attempt.attempt_number
        )
        await observer.observe_window(
            10.0, [recipient_target_channel, *recipient_sibling_channels]
        )
        reply_views = observer.matching(reply_digest)
        reply_total = len(reply_views)
        reuse_note_ok = "no new text was submitted" in (redrive_result.note or "")
        accepted_reply_ref = reply_result.native_message_id
        ledger_reused = bool(
            redrive_ledger.get("native_ref")
            and redrive_ledger.get("native_ref") == reply_ledger.get("native_ref")
            and redrive_ledger.get("native_ref") == accepted_reply_ref
        )
        # Fresh retained-history observation, not the observer's accumulated
        # pre-restart events. A missing reply after redrive must fail.
        retained = await recipient_session.call(
            "history", chat_type="group", chat_id=target_recipient, before=None
        )
        retained_rows = (
            retained.get("chatItems", []) if isinstance(retained, dict) else []
        )
        retained_reply_after_redrive = len(reply_views) == 1 and _fresh_reply_retained(
            retained_rows,
            channel=recipient_target_channel,
            reply=reply_views[0],
            parent=parent_view,
        )

        await recipient_session.stop()
        recipient_lifecycle["stopped_ns"] = time.monotonic_ns()
        await redrive_adapter.stop()
        redrive_adapter = None
        sender["stopped_ns"] = time.monotonic_ns()

        parent_views = observer.matching(parent_digest)
        sibling_hits = observer.sibling_matches(
            parent_digest, recipient_sibling_channels
        )
        journal_entry = next(
            (
                entry
                for entry in _journal_entries(state_root / "sender")
                if entry.get("text_sha256") == transient_digest
            ),
            None,
        )
        record = {
            "format": FORMAT,
            "version": VERSION,
            "core_commit": core_commit,
            "provider_commit": provider_pin,
            "sdk_version": sdk,
            "adapter_id": "qualifytwo",
            "sender_profile_tag": sender_tag,
            "recipient_profile_tag": recipient_tag,
            "sender_worker": sender,
            "recipient_worker": recipient_lifecycle,
            "target_channel": target_channel,
            "recipient_target_channel": recipient_target_channel,
            "sibling_channels": sibling_channels,
            "recipient_sibling_channels": recipient_sibling_channels,
            "roster_member_sha256s": roster,
            "servers_local_only": True,
            "provider_src_matches_pin": provider_src_matches_pin,
            "harness_commit": harness_commit,
            "cases": {
                "group_send_observed": {
                    "passed": bool(
                        parent_view is not None
                        and parent_view["channel"] == recipient_target_channel
                        and parent_view["text_sha256"] == parent_digest
                        and not sibling_hits
                        and parent_ledger.get("accepted") is True
                    ),
                    "sent_text_sha256": parent_digest,
                    "send_ns": parent_send_ns,
                    "ledger": parent_ledger,
                    "expected_attempt": {
                        "event_id": parent_attempt.event_id,
                        "outbox_id": parent_attempt.outbox_id,
                        "attempt_number": parent_attempt.attempt_number,
                    },
                    "observations": parent_views,
                    "sibling_observations": sibling_hits,
                },
                "native_quote_target": {
                    "passed": bool(
                        observer.quoted_reply(
                            reply_digest, parent_view["item_id"], parent_digest
                        )
                        and not observer.sibling_matches(
                            reply_digest, recipient_sibling_channels
                        )
                        and reply_ledger.get("accepted") is True
                    ),
                    "sent_text_sha256": reply_digest,
                    "send_ns": reply_send_ns,
                    "ledger": reply_ledger,
                    "expected_attempt": {
                        "event_id": reply_attempt.event_id,
                        "outbox_id": reply_attempt.outbox_id,
                        "attempt_number": reply_attempt.attempt_number,
                    },
                    "observations": reply_views,
                    "sibling_observations": observer.sibling_matches(
                        reply_digest, recipient_sibling_channels
                    ),
                    "parent_item_id": parent_view["item_id"],
                    "parent_text_sha256": parent_digest,
                },
                "restart_continuation": {
                    "passed": bool(
                        reuse_note_ok
                        and ledger_reused
                        and reply_total == 1
                        and sender["redrive_pid"] != sender["pid"]
                        and retained_reply_after_redrive
                        and new_sends == 0
                    ),
                    "redrive_ns": redrive_ns,
                    "kill_ns": kill_ns,
                    "reused_native_ref": redrive_ledger.get("native_ref", ""),
                    "ledger": redrive_ledger,
                    "reply_text_sha256": reply_digest,
                    "reply_observations_total": reply_total,
                    "reply_retained_after_redrive": retained_reply_after_redrive,
                    "new_sends": new_sends,
                },
                "transient_worker_admission": {
                    "passed": bool(
                        journal_entry
                        and journal_entry.get("event_kind") == "message.created"
                    ),
                    "sent_text_sha256": transient_digest,
                    "recipient_send_ns": recipient_send_ns,
                    "journal_entry": journal_entry,
                },
            },
        }
    finally:
        if redrive_adapter is not None:
            try:
                await redrive_adapter.stop()
            except Exception:
                pass
        if sender_adapter is not None:
            try:
                await sender_adapter.stop()
            except Exception:
                pass
        if recipient_session is not None and recipient_lifecycle["stopped_ns"] is None:
            try:
                await recipient_session.stop()
            except Exception:
                pass
        if recipient_lifecycle["stopped_ns"] is None:
            recipient_lifecycle["stopped_ns"] = time.monotonic_ns()
        if sender["stopped_ns"] is None:
            sender["stopped_ns"] = time.monotonic_ns()
    if record is None:
        raise RuntimeError("The campaign ended without a record")
    verdict = evaluate(
        record,
        expected_core_commit=expected_core_commit,
        expected_provider_commit=expected_provider_commit,
    )
    record_path = root / "verdict.json"
    await asyncio.to_thread(
        record_path.write_text, json.dumps(record, indent=2, sort_keys=True), "utf-8"
    )
    for reason in verdict:
        failures.append(f"verdict refusal: {reason}")
    for name, case in record["cases"].items():
        if case.get("passed") is not True:
            failures.append(f"case not passed: {name}")
    return record, failures


_Observer_placeholder = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--template", action="store_true", help="Print an unqualified evidence template"
    )
    mode.add_argument(
        "--check-sdk", action="store_true", help="Verify the installed campaign SDK"
    )
    mode.add_argument(
        "--run", action="store_true", help="Run the live two-profile tier"
    )
    mode.add_argument(
        "--validate",
        metavar="FILE",
        type=Path,
        help="Validate a two-profile observation record",
    )
    parser.add_argument("--server", help="Local server address used by --run")
    parser.add_argument(
        "--root", type=Path, help="Private campaign directory used by --run"
    )
    parser.add_argument("--core-commit", help="Expected immutable core revision")
    parser.add_argument(
        "--provider-commit", help="Expected immutable provider revision"
    )
    args = parser.parse_args(argv)
    if (
        args.validate is None
        and not args.run
        and (args.core_commit is not None or args.provider_commit is not None)
    ):
        parser.error("Revision selectors apply only to --validate and --run")
    if args.template or args.check_sdk:
        try:
            actual = check_installed_sdk()
        except ValueError as exc:
            print(f"Unqualified: {exc}", file=sys.stderr)
            return 1
        if args.template:
            record = template()
            record["sdk_version"] = actual
            print(json.dumps(record, indent=2))
        else:
            print(f"Installed simplex-chat matches declared pin: {actual}")
        return 0
    if any(
        not isinstance(ref, str) or not COMMIT.fullmatch(ref) or ref == "0" * 40
        for ref in (args.core_commit, args.provider_commit)
    ):
        parser.error(
            "--validate and --run require non-null immutable --core-commit and "
            "--provider-commit"
        )
    if args.run:
        if not args.server or not args.root:
            parser.error("--run requires --server and --root")
        record, failures = asyncio.run(
            run_campaign(
                server=args.server,
                root=args.root,
                expected_core_commit=args.core_commit,
                expected_provider_commit=args.provider_commit,
            )
        )
        for failure in failures:
            print(f"Unqualified: {failure}", file=sys.stderr)
        return 1 if failures else 0
    try:
        path: Path = args.validate
        if not path.is_file():
            raise ValueError("Evidence must be a regular file")
        with path.open("rb") as handle:
            payload = handle.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("Evidence record exceeds the 1 MiB limit")
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        print(f"Evidence not usable: {type(exc).__name__}", file=sys.stderr)
        return 2
    errors = evaluate(
        parsed,
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
