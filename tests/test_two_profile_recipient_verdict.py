"""Offline refusals for two-profile recipient records, not observation authenticity."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/qualify_two_profile_recipient.py"
spec = importlib.util.spec_from_file_location("simplex_two_profile_gate", PATH)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

CORE = "b" * 40
PROVIDER = "a" * 40
HARNESS = "c" * 40
DIGEST_A = "1" * 64
DIGEST_B = "2" * 64
DIGEST_C = "3" * 64
DIGEST_D = "4" * 64


def observation(**updates):
    base = {
        "channel": "group:6",
        "item_id": 21,
        "text_sha256": DIGEST_A,
        "sender_member_sha256": DIGEST_B,
        "quoted_item_id": None,
        "quoted_text_sha256": None,
        "observed_ns": 1300,
    }
    base.update(updates)
    return base


def ledger(**updates):
    base = {
        "event_id": "event-p",
        "outbox_id": "outbox-p",
        "attempt_number": 1,
        "channel": "group:5",
        "native_ref": "lease:user:1:group:5:item:41",
        "native_item_id": 41,
        "accepted": True,
    }
    base.update(updates)
    return base


def qualified_record():
    record = gate.template()
    record.update(
        core_commit=CORE,
        provider_commit=PROVIDER,
        adapter_id="qualifytwo",
        sender_profile_tag="sender-tag",
        recipient_profile_tag="recipient-tag",
        target_channel="group:5",
        recipient_target_channel="group:6",
        sibling_channels=["group:7"],
        recipient_sibling_channels=["group:8"],
        roster_member_sha256s={
            "group:6": [DIGEST_B, DIGEST_C],
            "group:8": [DIGEST_B, DIGEST_C],
        },
        servers_local_only=True,
        provider_src_matches_pin=True,
        harness_commit=HARNESS,
    )
    record["sender_worker"] = {
        "pid": 100,
        "ready_ns": 1100,
        "exited_ns": 1600,
        "exit_signal": "SIGKILL",
        "redrive_pid": 200,
        "redrive_ready_ns": 1800,
        "stopped_ns": 2400,
    }
    record["recipient_worker"] = {"pid": 300, "ready_ns": 1050, "stopped_ns": 2300}
    cases = record["cases"]
    cases["group_send_observed"] = {
        "passed": True,
        "sent_text_sha256": DIGEST_A,
        "send_ns": 1200,
        "ledger": ledger(),
        "observations": [observation()],
        "sibling_observations": [],
    }
    cases["native_quote_target"] = {
        "passed": True,
        "sent_text_sha256": DIGEST_D,
        "send_ns": 1400,
        "ledger": ledger(
            event_id="event-r",
            outbox_id="outbox-r",
            native_ref="lease:user:1:group:5:item:43",
            native_item_id=43,
        ),
        "observations": [
            observation(
                item_id=44,
                text_sha256=DIGEST_D,
                quoted_item_id=21,
                quoted_text_sha256=DIGEST_A,
                observed_ns=1500,
            )
        ],
        "sibling_observations": [],
        "parent_item_id": 21,
        "parent_text_sha256": DIGEST_A,
    }
    cases["restart_continuation"] = {
        "passed": True,
        "redrive_ns": 1900,
        "kill_ns": 1550,
        "reused_native_ref": "lease:user:1:group:5:item:43",
        "ledger": ledger(
            event_id="event-r",
            outbox_id="outbox-r",
            native_ref="lease:user:1:group:5:item:43",
            native_item_id=43,
        ),
        "reply_text_sha256": DIGEST_D,
        "reply_observations_total": 1,
        "new_sends": 0,
    }
    cases["transient_worker_admission"] = {
        "passed": True,
        "sent_text_sha256": DIGEST_C,
        "recipient_send_ns": 1700,
        "journal_entry": {
            "event_id": "event-t",
            "text_sha256": DIGEST_C,
            "provenance": "recovered",
            "channel": "group:6",
            "event_kind": "message.created",
            "admitted_ns": 1850,
        },
    }
    return record


def validate(record):
    return gate.evaluate(
        record, expected_core_commit=CORE, expected_provider_commit=PROVIDER
    )


def test_template_is_deliberately_unqualified():
    assert validate(gate.template())


def test_structurally_complete_record_is_accepted():
    assert validate(qualified_record()) == ()


def test_stale_revisions_are_refused():
    for field, value in (
        ("core_commit", "d" * 40),
        ("provider_commit", "d" * 40),
        ("sdk_version", "not-the-pinned-version"),
        ("provider_src_matches_pin", False),
    ):
        record = qualified_record()
        record[field] = value
        assert validate(record), field
    record = qualified_record()
    record["harness_commit"] = "deadbeef"
    assert validate(record)


def test_identical_profile_tags_are_not_independent():
    record = qualified_record()
    record["recipient_profile_tag"] = record["sender_profile_tag"]
    assert validate(record)


def test_link_like_profile_tags_are_refused():
    record = qualified_record()
    record["sender_profile_tag"] = "sender@smp://host"
    assert validate(record)


def test_unknown_top_level_fields_are_refused():
    record = qualified_record()
    record["unexpected"] = "do-not-echo"
    assert validate(record)


def test_local_server_restriction_is_required():
    record = qualified_record()
    record["servers_local_only"] = False
    assert validate(record)


def test_recipient_lifecycle_must_cover_the_campaign():
    record = qualified_record()
    record["recipient_worker"]["stopped_ns"] = 1150
    reasons = validate(record)
    assert any("after the recipient observer stopped" in reason for reason in reasons)
    record = qualified_record()
    record["recipient_worker"]["ready_ns"] = 1900
    assert any("was ready" in reason for reason in validate(record))


@pytest.mark.parametrize(
    "updates",
    [
        {"accepted": False},
        {"channel": "group:7"},
        {"native_ref": "lease:user:1:group:5:item:99"},
        {"native_item_id": 0},
        {"event_id": ""},
        {"attempt_number": 0},
    ],
)
def test_ledger_disagreement_is_refused(updates):
    record = qualified_record()
    record["cases"]["group_send_observed"]["ledger"] = ledger(**updates)
    assert validate(record)


def test_observed_plaintext_must_match_the_send():
    record = qualified_record()
    record["cases"]["group_send_observed"]["observations"][0]["text_sha256"] = DIGEST_D
    assert any("plaintext differs" in reason for reason in validate(record))


def test_observation_on_a_different_channel_is_refused():
    record = qualified_record()
    record["cases"]["group_send_observed"]["observations"][0]["channel"] = "group:8"
    reasons = validate(record)
    assert any("not on the target channel" in reason for reason in reasons)


def test_sibling_delivery_is_a_refusal():
    record = qualified_record()
    record["cases"]["group_send_observed"]["sibling_observations"] = [observation()]
    assert any("sibling destination received" in reason for reason in validate(record))


def test_duplicate_observation_of_one_native_item_is_refused():
    record = qualified_record()
    record["cases"]["native_quote_target"]["observations"][0]["item_id"] = 21
    record["cases"]["native_quote_target"]["observations"][0]["text_sha256"] = DIGEST_D
    assert any("duplicate observation" in reason for reason in validate(record))


def test_unknown_peer_observation_is_refused():
    record = qualified_record()
    record["cases"]["group_send_observed"]["observations"][0][
        "sender_member_sha256"
    ] = ("f" * 64)
    assert any("outside the recipient roster" in reason for reason in validate(record))


def test_multiple_observations_are_refused():
    record = qualified_record()
    record["cases"]["group_send_observed"]["observations"].append(
        observation(item_id=22)
    )
    assert any("exactly one" in reason for reason in validate(record))


@pytest.mark.parametrize(
    "updates",
    [
        {"quoted_item_id": None, "quoted_text_sha256": None},
        {"quoted_item_id": 99, "quoted_text_sha256": DIGEST_A},
        {"quoted_item_id": 21, "quoted_text_sha256": DIGEST_B},
        {"quoted_item_id": 21},
    ],
)
def test_wrong_or_missing_quote_targets_are_refused(updates):
    record = qualified_record()
    record["cases"]["native_quote_target"]["observations"][0] = observation(
        item_id=44, text_sha256=DIGEST_D, **updates
    )
    reasons = validate(record)
    assert any("quote" in reason for reason in reasons)


def test_quote_target_not_bound_to_the_observed_parent_is_refused():
    record = qualified_record()
    record["cases"]["native_quote_target"]["parent_item_id"] = 30
    assert any("targets another message" in reason for reason in validate(record))


def test_restart_without_a_fresh_worker_is_refused():
    record = qualified_record()
    record["sender_worker"]["redrive_pid"] = record["sender_worker"]["pid"]
    assert any("fresh worker" in reason for reason in validate(record))


def test_restart_resubmission_is_refused():
    record = qualified_record()
    record["cases"]["restart_continuation"]["new_sends"] = 1
    assert any("must not submit new sends" in reason for reason in validate(record))


def test_restart_with_a_duplicated_reply_copy_is_refused():
    record = qualified_record()
    record["cases"]["restart_continuation"]["reply_observations_total"] = 2
    assert any("exactly one reply copy" in reason for reason in validate(record))


def test_restart_with_an_unreused_reference_is_refused():
    record = qualified_record()
    record["cases"]["restart_continuation"]["reused_native_ref"] = "lease:x:item:1"
    assert any("was not reused" in reason for reason in validate(record))


def test_restart_before_the_injected_failure_is_refused():
    record = qualified_record()
    record["cases"]["restart_continuation"]["redrive_ns"] = 1500
    assert any("follow the worker exit" in reason for reason in validate(record))


def test_transient_send_outside_the_worker_failure_window_is_refused():
    record = qualified_record()
    record["cases"]["transient_worker_admission"]["recipient_send_ns"] = 1200
    assert any(
        "while the sender worker is down" in reason for reason in validate(record)
    )


def test_missing_transient_admission_is_refused():
    record = qualified_record()
    record["cases"]["transient_worker_admission"]["journal_entry"] = None
    assert any("durable admission" in reason for reason in validate(record))


@pytest.mark.parametrize(
    "updates",
    [
        {"text_sha256": DIGEST_D},
        {"provenance": "history"},
        {"channel": "group:8"},
        {"event_id": ""},
        {"admitted_ns": None},
    ],
)
def test_contradictory_admission_journal_entries_are_refused(updates):
    record = qualified_record()
    entry = dict(record["cases"]["transient_worker_admission"]["journal_entry"])
    entry.update(updates)
    record["cases"]["transient_worker_admission"]["journal_entry"] = entry
    assert validate(record)


def test_each_case_is_mandatory():
    for name in gate.CASES:
        record = qualified_record()
        record["cases"][name]["passed"] = False
        assert validate(record), name


def test_cli_validation_refuses_incomplete_records(tmp_path, capsys):
    path = tmp_path / "record.json"
    path.write_text(json.dumps(gate.template()))
    assert (
        gate.main(
            [
                "--validate",
                str(path),
                "--core-commit",
                CORE,
                "--provider-commit",
                PROVIDER,
            ]
        )
        == 1
    )
    assert "Unqualified" in capsys.readouterr().err


def test_cli_validation_requires_explicit_target_revisions(tmp_path):
    path = tmp_path / "record.json"
    path.write_text("{}")
    with pytest.raises(SystemExit) as exc:
        gate.main(["--validate", str(path)])
    assert exc.value.code == 2


def test_cli_duplicate_fields_refuse_without_echoing_record(tmp_path, capsys):
    path = tmp_path / "duplicate.json"
    path.write_text('{"format":"private-marker","format":"contradictory"}')
    assert (
        gate.main(
            [
                "--validate",
                str(path),
                "--core-commit",
                CORE,
                "--provider-commit",
                PROVIDER,
            ]
        )
        == 2
    )
    assert "private-marker" not in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["--template", "--check-sdk"])
def test_campaign_setup_refuses_wrong_installed_sdk(mode, monkeypatch, capsys):
    monkeypatch.setattr(gate.importlib.metadata, "version", lambda _: "stale")
    assert gate.main([mode]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "does not match" in output.err


@pytest.mark.parametrize("mode", ["--template", "--check-sdk"])
def test_campaign_setup_refuses_missing_sdk(mode, monkeypatch, capsys):
    def missing(name):
        raise gate.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(gate.importlib.metadata, "version", missing)
    assert gate.main([mode]) == 1
    assert not capsys.readouterr().out


def test_installed_sdk_check_accepts_exact_pin(monkeypatch, capsys):
    monkeypatch.setattr(
        gate.importlib.metadata, "version", lambda _: gate._pinned_sdk()
    )
    assert gate.main(["--check-sdk"]) == 0
    assert gate._pinned_sdk() in capsys.readouterr().out
