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


def validate(sender, peer, result):
    assert result["observer_complete"] is True
    assert sender[0]["kind"] == "campaign_started"
    assert sender[-1]["kind"] == "campaign_passed"
    assert peer[0]["kind"] == "observer_started"
    assert peer[-1]["kind"] == "observer_stopped"
    assert not any(row["kind"] == "observer_failure" for row in peer)
    for stream in [sender, peer]:
        assert all(
            datetime.fromisoformat(row["utc"]).utcoffset() is not None for row in stream
        )
        assert all(
            a["monotonic_ns"] < b["monotonic_ns"]
            for a, b in zip(stream, stream[1:], strict=False)
        )
    teardown = next(row for row in sender if row["kind"] == "campaign_teardown")
    assert teardown["observer_returncode"] == 0 and teardown["errors"] == []
    starts = {row["label"]: row for row in sender if row["kind"] == "worker_started"}
    stops = {row["label"]: row for row in sender if row["kind"] == "worker_stopped"}
    assert set(starts) == set(stops)
    assert len({row["pid"] for row in starts.values()}) == len(starts) == 5
    for label in starts:
        assert stops[label]["pid"] == starts[label]["pid"]
        assert stops[label]["returncode"] is not None
        assert starts[label]["monotonic_ns"] < stops[label]["monotonic_ns"]
    assert (
        stops["restart-first"]["monotonic_ns"]
        < starts["restart-redrive"]["monotonic_ns"]
    )
    accepted = next(row for row in sender if row["kind"] == "accepted_handoff")
    reused = next(row for row in sender if row["kind"] == "accepted_redrive_reused")
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
    drained = next(row for row in sender if row["kind"] == "send_command_drained")
    killed = next(row for row in sender if row["kind"] == "owned_worker_sigkill")
    sealed = next(row for row in sender if row["kind"] == "uncertain_reservation")
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
    calls = [row for row in sender if row["kind"] == "native_send_call"]
    for case, expected in [(restart, "restart-first"), (fence, "fence-first")]:
        handoff_calls = [row for row in calls if row["nonce"] == case["nonce"]]
        assert len(handoff_calls) == 1 and handoff_calls[0]["label"] == expected
    snapshots = [row for row in peer if row["kind"] == "recipient_snapshot"]
    assert snapshots
    last = snapshots[-1]["items"]
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
    intended_text = intended_parents[0]["text"]
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
                if case is restart and observation["kind"] == "recipient_snapshot":
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
    assert set(by_nonce) == {restart["nonce"] + "-barrier", fence["nonce"] + "-barrier"}
    for (case, operation), begin, end in zip(
        [(restart, reused), (fence, retries[-1])], windows, ends, strict=True
    ):
        barrier = by_nonce[case["nonce"] + "-barrier"]
        assert (
            operation["monotonic_ns"] < barrier["monotonic_ns"] < begin["monotonic_ns"]
        )
        assert any(
            snapshot["monotonic_ns"] <= barrier["monotonic_ns"]
            and any(
                ("nonce=" + barrier["nonce"]) in item["text"].split()
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


def test_normal_runtime_uses_the_advertised_native_reply_without_override():
    manifest, _, _, _ = load()
    result = json.loads((ROOT / "capture/planned-reply.json").read_text())
    peer = [
        json.loads(line)
        for line in (ROOT / "capture/planned-recipient.jsonl").read_text().splitlines()
    ]
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
    assert result["native_strategy_override"] is False
    assert (
        result["delivery_strategy"] == "direct" and result["receipt_status"] == "sent"
    )
    assert (
        peer[0]["kind"] == "observer_started" and peer[-1]["kind"] == "observer_stopped"
    )
    assert not any(row["kind"] == "observer_failure" for row in peer)
    snapshot = [row for row in peer if row["kind"] == "recipient_snapshot"][-1]
    matches = [
        row
        for row in snapshot["items"]
        if ("nonce=" + result["nonce"]) in row["text"].split()
    ]
    assert matches == [result["recipient"]]
    assert matches[0]["quote_id"] == 28 and matches[0]["quote_text"]
