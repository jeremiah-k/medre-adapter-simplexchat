"""Consistency and causal ordering of retained native captures, not authenticity."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

ROOT = (
    Path(__file__).resolve().parents[1]
    / "docs/evidence/native-reply-boundaries-sdk-7.1.3-2026-11-16"
)


def load():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert set(manifest["capture_sha256"]) == {
        "sender.jsonl",
        "recipient.jsonl",
        "results.json",
        "planned-reply.json",
        "planned-recipient.jsonl",
    }
    for name, digest in manifest["capture_sha256"].items():
        assert (
            hashlib.sha256((ROOT / "capture" / name).read_bytes()).hexdigest() == digest
        )
    sender = [
        json.loads(line)
        for line in (ROOT / "capture/sender.jsonl").read_text().splitlines()
    ]
    peer = [
        json.loads(line)
        for line in (ROOT / "capture/recipient.jsonl").read_text().splitlines()
    ]
    result = json.loads((ROOT / "capture/results.json").read_text())
    return manifest, sender, peer, result


def _one(stream, kind):
    matches = [row for row in stream if row["kind"] == kind]
    assert len(matches) == 1
    return matches[0]


def _intended_parent_text():
    original = (
        ROOT.parent
        / "native-quotes-sdk-7.1.3-2026-11-15/evidence/case6-ambiguous-fenced.json"
    )
    parent_capture = json.loads(original.read_text())
    intended_parents = [
        row
        for row in parent_capture["sender_native_history_message_items"]
        if row["itemId"] == 30
    ]
    assert len(intended_parents) == 1
    return intended_parents[0]["text"]


def _validate_observer(peer, sdk):
    assert peer[0] is _one(peer, "observer_started")
    assert peer[-1] is _one(peer, "observer_stopped")
    assert peer[0]["pid"] == peer[-1]["pid"]
    assert peer[0]["sdk"] == sdk
    assert all(
        row["kind"]
        in {
            "observer_started",
            "observer_stopped",
            "recipient_event",
            "recipient_snapshot",
        }
        for row in peer
    )
    assert all(
        datetime.fromisoformat(row["utc"]).utcoffset() is not None for row in peer
    )
    assert all(
        a["monotonic_ns"] < b["monotonic_ns"]
        for a, b in zip(peer, peer[1:], strict=False)
    )


def validate(sender, peer, result):
    assert result["observer_complete"] is True
    assert sender[0] is _one(sender, "campaign_started")
    assert sender[-1] is _one(sender, "campaign_passed")
    assert sender[0]["sources"] == result["sources"]
    assert sender[0]["sdk"] == result["sdk"]
    assert sender[0]["tag"] == result["tag"]
    assert _one(sender, "cases_passed")["results"] == result["cases"]
    _validate_observer(peer, result["sdk"])
    assert all(
        datetime.fromisoformat(row["utc"]).utcoffset() is not None for row in sender
    )
    assert all(
        a["monotonic_ns"] < b["monotonic_ns"]
        for a, b in zip(sender, sender[1:], strict=False)
    )
    teardown = _one(sender, "campaign_teardown")
    assert peer[-1]["monotonic_ns"] < teardown["monotonic_ns"]
    assert teardown["observer_returncode"] == 0 and teardown["errors"] == []
    starts = {row["label"]: row for row in sender if row["kind"] == "worker_started"}
    stops = {row["label"]: row for row in sender if row["kind"] == "worker_stopped"}
    assert (
        set(starts)
        == set(stops)
        == {
            "restart-first",
            "restart-redrive",
            "fence-first",
            "fence-retry-2",
            "fence-retry-3",
        }
    )
    assert sum(row["kind"] == "worker_started" for row in sender) == len(starts)
    assert sum(row["kind"] == "worker_stopped" for row in sender) == len(stops)
    assert len({row["pid"] for row in starts.values()}) == len(starts) == 5
    assert peer[0]["pid"] not in {row["pid"] for row in starts.values()}
    for label in starts:
        assert stops[label]["pid"] == starts[label]["pid"]
        assert stops[label]["returncode"] == (-9 if label == "fence-first" else 0)
        assert starts[label]["monotonic_ns"] < stops[label]["monotonic_ns"]
    assert (
        stops["restart-first"]["monotonic_ns"]
        < starts["restart-redrive"]["monotonic_ns"]
    )
    accepted = _one(sender, "accepted_handoff")
    reused = _one(sender, "accepted_redrive_reused")
    assert accepted["case"] == reused["case"] == "restart"
    assert accepted["native_id"]
    assert reused["native_id"] == accepted["native_id"]
    assert reused["request_sha256"] == accepted["request_sha256"]
    assert reused["native_send_calls"] == 0
    assert reused["pid"] == starts["restart-redrive"]["pid"]
    assert reused["prior_pid"] == starts["restart-first"]["pid"]
    assert (
        starts["restart-redrive"]["monotonic_ns"]
        < reused["monotonic_ns"]
        < stops["restart-redrive"]["monotonic_ns"]
    )
    drained = _one(sender, "send_command_drained")
    killed = _one(sender, "owned_worker_sigkill")
    sealed = _one(sender, "uncertain_reservation")
    assert drained["pid"] == killed["pid"] == starts["fence-first"]["pid"]
    assert drained["monotonic_ns"] < killed["monotonic_ns"] < sealed["monotonic_ns"]
    assert stops["fence-first"]["returncode"] == -9
    retries = [row for row in sender if row["kind"] == "retry_fenced"]
    assert [row["retry"] for row in retries] == [2, 3]
    previous_stop = stops["fence-first"]
    for row in retries:
        worker = starts[f"fence-retry-{row['retry']}"]
        worker_stop = stops[f"fence-retry-{row['retry']}"]
        assert row["pid"] == worker["pid"]
        assert sealed["monotonic_ns"] < previous_stop["monotonic_ns"]
        assert previous_stop["monotonic_ns"] < worker["monotonic_ns"]
        assert (
            worker["monotonic_ns"] < row["monotonic_ns"] < worker_stop["monotonic_ns"]
        )
        previous_stop = worker_stop
        assert row["error_type"] == "AdapterPermanentError"
        assert row["native_send_calls"] == 0
        assert row["request_sha256"] == sealed["request_sha256"]
        assert row["native_id"] is sealed["native_id"] is None
    restart = result["cases"]["restart_no_duplicate"]
    fence = result["cases"]["ambiguous_submission_fenced"]
    assert restart["passed"] is fence["passed"] is True
    assert restart["recipient_copies"] == 1
    assert restart["handoff_send_calls"] == [1, 0]
    assert fence["handoff_send_calls"] == [1, 0, 0]
    assert restart["worker_pids"] == [
        starts[label]["pid"] for label in ("restart-first", "restart-redrive")
    ]
    assert fence["worker_pids"] == [
        starts[label]["pid"]
        for label in ("fence-first", "fence-retry-2", "fence-retry-3")
    ]
    assert fence["reservation"] == {
        "request_sha256": sealed["request_sha256"],
        "native_id": None,
    }
    calls = [row for row in sender if row["kind"] == "native_send_call"]
    by_call_nonce = {row["nonce"]: row for row in calls}
    expected_calls = {
        restart["nonce"]: "restart-first",
        fence["nonce"]: "fence-first",
        restart["nonce"] + "-barrier": "restart-redrive",
        fence["nonce"] + "-barrier": "fence-retry-3",
    }
    assert len(calls) == len(by_call_nonce) == len(expected_calls)
    assert set(by_call_nonce) == set(expected_calls)
    for nonce, label in expected_calls.items():
        call = by_call_nonce[nonce]
        assert call["label"] == label and call["pid"] == starts[label]["pid"]
        assert call["ordinal"] == 1
        assert (
            starts[label]["monotonic_ns"]
            < call["monotonic_ns"]
            < stops[label]["monotonic_ns"]
        )
        assert peer[0]["monotonic_ns"] < call["monotonic_ns"]
    assert (
        by_call_nonce[restart["nonce"]]["monotonic_ns"]
        < accepted["monotonic_ns"]
        < stops["restart-first"]["monotonic_ns"]
    )
    assert by_call_nonce[fence["nonce"]]["monotonic_ns"] < drained["monotonic_ns"]
    snapshots = [row for row in peer if row["kind"] == "recipient_snapshot"]
    assert snapshots
    last = snapshots[-1]["items"]
    intended_text = _intended_parent_text()
    assert restart["quote_id"] == 28
    for case, maximum, expected in [
        (restart, 1, 1),
        (fence, 1, fence["recipient_copies"]),
    ]:
        copies = [
            row for row in last if ("nonce=" + case["nonce"]) in row["text"].split()
        ]
        assert len(copies) == expected <= maximum
        seen_ids = set()
        for observation in peer:
            observed_items = observation.get("items", [])
            if observation["kind"] == "recipient_event":
                observed_items = [observation]
            matches = [
                item
                for item in observed_items
                if ("nonce=" + case["nonce"]) in item["text"].split()
            ]
            assert len(matches) <= maximum
            for item in matches:
                assert type(item["item_id"]) is int and item["item_id"] > 0
                seen_ids.add(item["item_id"])
                call = by_call_nonce[case["nonce"]]
                assert observation["monotonic_ns"] > call["monotonic_ns"]
                assert (
                    hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
                    == call["body_sha256"]
                )
                assert item["quote_id"] == restart["quote_id"]
                assert item["quote_text"] == intended_text
        # A duplicate disappearing from a later snapshot cannot erase its observation.
        assert len(seen_ids) == expected
        assert seen_ids == {item["item_id"] for item in copies}
    windows = [row for row in sender if row["kind"] == "observation_window_started"]
    ends = [row for row in sender if row["kind"] == "observation_window_finished"]
    barriers = [row for row in sender if row["kind"] == "barrier_received"]
    assert len(windows) == len(ends) == len(barriers) == 2
    by_nonce = {row["nonce"]: row for row in barriers}
    submissions = [row for row in sender if row["kind"] == "barrier_submitted"]
    by_submission = {row["nonce"]: row for row in submissions}
    assert len(submissions) == len(by_submission) == 2
    assert set(by_submission) == set(by_nonce)
    assert set(by_nonce) == {restart["nonce"] + "-barrier", fence["nonce"] + "-barrier"}
    for (case, operation), begin, end in zip(
        [(restart, reused), (fence, retries[-1])], windows, ends, strict=True
    ):
        barrier = by_nonce[case["nonce"] + "-barrier"]
        call = by_call_nonce[barrier["nonce"]]
        assert (
            operation["monotonic_ns"]
            < call["monotonic_ns"]
            < by_submission[barrier["nonce"]]["monotonic_ns"]
            < barrier["monotonic_ns"]
            < begin["monotonic_ns"]
        )
        assert any(
            call["monotonic_ns"] < snapshot["monotonic_ns"] <= barrier["monotonic_ns"]
            and any(
                ("nonce=" + barrier["nonce"]) in item["text"].split()
                and hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
                == call["body_sha256"]
                for item in snapshot["items"]
            )
            for snapshot in snapshots
        )
        assert end["monotonic_ns"] - begin["monotonic_ns"] >= 10_000_000_000
        before = [
            row for row in snapshots if row["monotonic_ns"] <= begin["monotonic_ns"]
        ]
        after = [row for row in snapshots if row["monotonic_ns"] >= end["monotonic_ns"]]
        assert before and after
        covering = [
            row
            for row in snapshots
            if before[-1]["monotonic_ns"]
            <= row["monotonic_ns"]
            <= after[0]["monotonic_ns"]
        ]
        assert all(
            b["monotonic_ns"] - a["monotonic_ns"] <= 3_000_000_000
            for a, b in zip(covering, covering[1:], strict=False)
        )


def test_executed_boundaries_and_scope_are_consistent():
    manifest, sender, peer, result = load()
    assert (
        manifest["tested_provider_commit"]
        == result["sources"]["simplexchat"]
        == "a049e2f3264c76d57b2105b796ceda25b1bc6c90"
    )
    assert (
        manifest["tested_core_commit"]
        == result["sources"]["core"]
        == "989f44336e1f009ca89d7b50c8bbe0794ca251a6"
    )
    assert manifest["sdk_version"] == result["sdk"] == "7.1.3"
    validate(sender, peer, result)


@pytest.mark.parametrize(
    "bad",
    [
        "unrelated_exception",
        "extra_send",
        "missing_observer_end",
        "missing_reservation",
        "duplicate_recipient",
        "retry_before_submission",
        "retry_wrong_worker",
        "observation_before_retry",
        "wrong_barrier",
        "intermediate_duplicate",
        "rotated_recipient_id",
        "wrong_parent_id",
        "wrong_parent_text",
        "wrong_event_parent",
        "extra_unrelated_send",
        "wrong_send_pid",
        "wrong_sent_body_hash",
        "wrong_observer_pid",
        "wrong_worker_exit",
        "wrong_summary_reservation",
        "wrong_summary_send_count",
        "missing_barrier_submission",
    ],
)
def test_corrupted_proof_cannot_satisfy_the_boundary_checks(bad):
    _, sender, peer, result = load()
    sender, peer, result = deepcopy(sender), deepcopy(peer), deepcopy(result)
    if bad == "unrelated_exception":
        next(row for row in sender if row["kind"] == "retry_fenced")[
            "error_type"
        ] = "ConnectionError"
    elif bad == "extra_send":
        row = next(row for row in sender if row["kind"] == "retry_fenced")
        row["native_send_calls"] = 1
    elif bad == "missing_observer_end":
        peer.pop()
    elif bad == "missing_reservation":
        next(row for row in sender if row["kind"] == "retry_fenced")[
            "native_id"
        ] = "changed"
    elif bad == "retry_before_submission":
        retry = next(row for row in sender if row["kind"] == "retry_fenced")
        sender.remove(retry)
        index = next(
            i for i, row in enumerate(sender) if row["kind"] == "send_command_drained"
        )
        retry["monotonic_ns"] = sender[index]["monotonic_ns"] - 1
        sender.insert(index, retry)
    elif bad == "retry_wrong_worker":
        next(row for row in sender if row["kind"] == "retry_fenced")["pid"] = next(
            row["pid"] for row in sender if row.get("label") == "fence-first"
        )
    elif bad == "observation_before_retry":
        # Move a complete, heartbeat-covered interval before the redrive.
        # Keep the stream sorted, its duration, and barrier-before-window order.
        selected = [
            row
            for row in sender
            if row["kind"]
            in {"accepted_redrive_reused", "worker_started", "worker_stopped"}
            and (
                row["kind"] == "accepted_redrive_reused"
                or row.get("label") == "restart-redrive"
            )
        ]
        end = next(
            row for row in sender if row["kind"] == "observation_window_finished"
        )
        selected.sort(key=lambda row: row["monotonic_ns"])
        for row in selected:
            sender.remove(row)
        index = sender.index(end) + 1
        for offset, row in enumerate(selected, 1):
            row["monotonic_ns"] = end["monotonic_ns"] + offset
            sender.insert(index + offset - 1, row)
    elif bad == "wrong_barrier":
        next(row for row in sender if row["kind"] == "barrier_received")[
            "nonce"
        ] = "unrelated-barrier"
    elif bad == "wrong_event_parent":
        next(row for row in peer if row["kind"] == "recipient_event")["quote_id"] = 99
    elif bad == "extra_unrelated_send":
        row = deepcopy(
            _one(
                [row for row in sender if row.get("label") == "restart-first"],
                "native_send_call",
            )
        )
        insert_at = next(
            i
            for i, existing in enumerate(sender)
            if existing["kind"] == "accepted_handoff"
        )
        row["nonce"] = "unaccounted-send"
        row["monotonic_ns"] += 1
        sender.insert(insert_at, row)
    elif bad == "wrong_send_pid":
        next(row for row in sender if row["kind"] == "native_send_call")["pid"] = 999
    elif bad == "wrong_sent_body_hash":
        next(row for row in sender if row["kind"] == "native_send_call")[
            "body_sha256"
        ] = ("0" * 64)
    elif bad == "wrong_observer_pid":
        peer[-1]["pid"] += 1
    elif bad == "wrong_worker_exit":
        next(row for row in sender if row["kind"] == "worker_stopped")["returncode"] = 1
    elif bad in {"wrong_summary_reservation", "wrong_summary_send_count"}:
        case = result["cases"]["ambiguous_submission_fenced"]
        if bad == "wrong_summary_reservation":
            case["reservation"]["request_sha256"] = "0" * 64
        else:
            case["handoff_send_calls"] = [1, 1, 0]
        _one(sender, "cases_passed")["results"] = deepcopy(result["cases"])
    elif bad == "missing_barrier_submission":
        sender.remove(next(row for row in sender if row["kind"] == "barrier_submitted"))
    else:
        snapshots = [row for row in peer if row["kind"] == "recipient_snapshot"]
        snapshot = snapshots[-1]
        if bad in {"intermediate_duplicate", "rotated_recipient_id"}:
            begin = next(
                row for row in sender if row["kind"] == "observation_window_started"
            )
            end = next(
                row for row in sender if row["kind"] == "observation_window_finished"
            )
            snapshot = next(
                row
                for row in snapshots
                if begin["monotonic_ns"] < row["monotonic_ns"] < end["monotonic_ns"]
            )
        duplicate = next(
            row
            for row in snapshot["items"]
            if row["text"].endswith(
                "nonce=" + result["cases"]["restart_no_duplicate"]["nonce"]
            )
        )
        if bad == "wrong_parent_id":
            duplicate["quote_id"] = 29
        elif bad == "wrong_parent_text":
            duplicate["quote_text"] = "unrelated parent"
        elif bad == "rotated_recipient_id":
            duplicate["item_id"] += 100
        else:
            extra = deepcopy(duplicate)
            extra["item_id"] += 100
            snapshot["items"].append(extra)
    with pytest.raises(AssertionError):
        validate(sender, peer, result)


def _load_planned():
    manifest, _, _, _ = load()
    result = json.loads((ROOT / "capture/planned-reply.json").read_text())
    peer = [
        json.loads(line)
        for line in (ROOT / "capture/planned-recipient.jsonl").read_text().splitlines()
    ]
    return manifest, result, peer


def _validate_planned(manifest, result, peer):
    assert (
        result["tested_provider_commit"]
        == manifest["planned_reply_provider_commit"]
        == "72328a92ea21229222ec04bd34745a19ac653c9a"
    )
    assert (
        result["adapter_source_sha256"]
        == manifest["planned_reply_adapter_source_sha256"]
    )
    assert result["tested_core_commit"] == manifest["tested_core_commit"]
    assert result["sdk_version"] == manifest["sdk_version"]
    assert result["native_strategy_override"] is False
    assert (
        result["delivery_strategy"] == "direct" and result["receipt_status"] == "sent"
    )
    _validate_observer(peer, result["sdk_version"])
    snapshots = [row for row in peer if row["kind"] == "recipient_snapshot"]
    assert snapshots
    intended = result["recipient"]
    assert intended["quote_id"] == 28
    assert intended["quote_text"] == _intended_parent_text()
    assert type(intended["item_id"]) is int and intended["item_id"] > 0
    seen_event = False
    for observation in peer:
        items = observation.get("items", [])
        if observation["kind"] == "recipient_event":
            items = [observation]
        matches = [
            item
            for item in items
            if ("nonce=" + result["nonce"]) in item["text"].split()
        ]
        assert len(matches) <= 1
        for match in matches:
            assert {key: match[key] for key in intended} == intended
            if observation["kind"] == "recipient_event":
                seen_event = True
    assert seen_event
    assert [
        item
        for item in snapshots[-1]["items"]
        if ("nonce=" + result["nonce"]) in item["text"].split()
    ] == [intended]


def test_normal_runtime_uses_the_advertised_native_reply_without_override():
    _validate_planned(*_load_planned())


@pytest.mark.parametrize(
    "bad",
    ["wrong_parent", "wrong_event", "hidden_duplicate", "wrong_sdk", "missing_event"],
)
def test_corrupted_planned_probe_is_rejected(bad):
    manifest, result, peer = _load_planned()
    event = next(row for row in peer if row["kind"] == "recipient_event")
    if bad == "wrong_parent":
        result["recipient"]["quote_text"] = "unrelated parent"
        event["quote_text"] = "unrelated parent"
        for snapshot in peer:
            for item in snapshot.get("items", []):
                item["quote_text"] = "unrelated parent"
    elif bad == "wrong_event":
        event["quote_id"] = 99
    elif bad == "hidden_duplicate":
        snapshot = next(row for row in peer if row["kind"] == "recipient_snapshot")
        duplicate = deepcopy(result["recipient"])
        duplicate["item_id"] += 100
        snapshot["items"].append(duplicate)
    elif bad == "wrong_sdk":
        peer[0]["sdk"] = "wrong"
    elif bad == "missing_event":
        peer.remove(event)
    with pytest.raises(AssertionError):
        _validate_planned(manifest, result, peer)
