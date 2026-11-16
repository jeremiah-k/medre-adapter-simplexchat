"""Offline consistency of fixed campaign captures, not observation authenticity."""

import json
from pathlib import Path

CAMPAIGN = (
    Path(__file__).resolve().parents[1]
    / "docs/evidence/native-quotes-sdk-7.1.3-2026-11-15"
)


def _read(name):
    return json.loads((CAMPAIGN / name).read_text(encoding="utf-8"))


def _capture(name):
    return _read(f"evidence/{name}.json")["items"]


def _single(rows, marker):
    matches = [row for row in rows if marker in (row.get("text") or "")]
    assert len(matches) == 1
    return matches[0]


def test_campaign_references_and_selected_revision_pair():
    record = _read("observations.json")
    assert record["provider_commit"] == "a049e2f3264c76d57b2105b796ceda25b1bc6c90"
    assert record["core_commit"] == "989f44336e1f009ca89d7b50c8bbe0794ca251a6"
    assert record["sender_profile_tag"] != record["recipient_profile_tag"]
    expected = {
        "direct_quote": "case1-direct-quote-recipient",
        "group_quote": "case2-group-quote-recipient",
        "origin_attribution": "case3-origin-attribution",
        "parent_expired_fallback": "case4-parent-fallback-recipient",
        "restart_no_duplicate": "case5-restart-no-duplicate-recipient",
        "ambiguous_submission_fenced": "case6-ambiguous-fenced",
    }
    assert set(record["cases"]) == set(expected)
    for case, name in expected.items():
        row = record["cases"][case]
        assert row["passed"] is row["recipient_observed"] is True
        assert row["evidence_ref"] == f"evidence/{name}.json"
        assert _read(row["evidence_ref"])


def test_direct_and_group_quote_targets_are_profile_local():
    for name, direction, parent_marker, reply_marker in (
        ("case1-direct-quote", "direct", "nonce=dp1-", "nonce=dr2-"),
        ("case2-group-quote", "group", "nonce=gpar-", "nonce=gq1-"),
    ):
        pairs = []
        for view, suffix in (("sender", "Snd"), ("recipient", "Rcv")):
            rows = _capture(f"{name}-{view}")
            parent, reply = _single(rows, parent_marker), _single(rows, reply_marker)
            assert parent["direction"] == reply["direction"] == direction + suffix
            assert reply["quotedItem"]["itemId"] == parent["itemId"]
            assert reply["quotedItem"]["text"] == parent["text"]
            pairs.append((parent["text"], reply["text"]))
        assert pairs[0] == pairs[1]


def test_attribution_summary_matches_recipient_captures():
    summary = _read("evidence/case3-origin-attribution.json")
    prefix = summary["checks"]["relay_prefix_readable"]
    for kind, name in (
        ("group", "case2-group-quote-recipient"),
        ("direct", "case1-direct-quote-recipient"),
    ):
        rows = {row["itemId"]: row for row in _capture(name)}
        for observed in summary[f"{kind}_items_recipient_view"]:
            captured = rows[observed["itemId"]]
            assert captured["text"].startswith(observed["text_prefix"])
            assert captured["text"].startswith(prefix)
            assert captured["direction"] == observed["direction"]
            if observed["sender_display"] is not None:
                assert captured["sender"]["displayName"] == observed["sender_display"]


def test_deleted_parent_fallback_and_restart_copies():
    deleted = _capture("case4-parent-deleted-sender")
    prior = _single(deleted, "nonce=gq1-")
    assert prior["quotedItem"]["itemId"] is None
    assert not any(
        "group_parent nonce=gpar-" in (row.get("text") or "") for row in deleted
    )
    fallback = []
    boundary = []
    for view in ("sender", "recipient"):
        reply = _single(_capture(f"case4-parent-fallback-{view}"), "nonce=fr1-")
        assert reply["quotedItem"] is None
        assert "[replying to: earlier upstream message]" in reply["text"]
        fallback.append(reply["text"])
        rows = _capture(f"case5-restart-no-duplicate-{view}")
        parent, reply = _single(rows, "nonce=gp2-"), _single(rows, "nonce=rs1-")
        assert reply["quotedItem"]["itemId"] == parent["itemId"]
        assert reply["quotedItem"]["text"] == parent["text"]
        boundary.append(reply["text"])
    assert fallback[0] == fallback[1]
    assert boundary[0] == boundary[1]


def test_fenced_summary_matches_retained_sender_history():
    summary = _read("evidence/case6-ambiguous-fenced.json")
    rows = _capture("case6-fenced-sender-history")
    by_id = {row["itemId"]: row for row in rows}
    assert (
        summary["recipient_observed_copies"]
        == len(summary["recipient_stream_matches"])
        == 0
    )
    assert summary["sender_native_history_us1_count"] == 0
    assert not any("nonce=us1-" in (row.get("text") or "") for row in rows)
    for observed in summary["sender_native_history_message_items"]:
        assert by_id[observed["itemId"]] == observed
    assert summary["fenced_ledger_row"]["native_id"] is None
    assert summary["redrive_attempts"] == ["attempt2 fenced", "attempt3 fenced"]
