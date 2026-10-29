"""Never promote native replies on SDK shape or self-generated proof alone."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/qualify_native_quotes.py"
spec = importlib.util.spec_from_file_location("simplex_quote_gate", PATH)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


CORE = "b" * 40
PROVIDER = "a" * 40


def validate(record):
    return gate.validate(
        record, expected_core_commit=CORE, expected_provider_commit=PROVIDER
    )


def qualified_record():
    record = gate.template()
    record.update(
        provider_commit="a" * 40,
        core_commit="b" * 40,
        sender_profile_tag="peer-A",
        recipient_profile_tag="peer-B",
    )
    for name in gate.CASES:
        record["cases"][name] = {
            "recipient_observed": True,
            "passed": True,
            "evidence_ref": f"local-redacted-observation/{name}.json",
        }
    return record


def test_template_is_deliberately_unqualified():
    assert validate(gate.template())


def test_structurally_complete_external_record():
    assert validate(qualified_record()) == ()


@pytest.mark.parametrize("missing", gate.CASES)
def test_each_case_is_mandatory(missing):
    record = qualified_record()
    record["cases"][missing]["recipient_observed"] = False
    assert validate(record)


def test_same_sender_and_recipient_is_not_independent():
    record = qualified_record()
    record["recipient_profile_tag"] = record["sender_profile_tag"]
    assert validate(record)


def test_stale_or_missing_sdk_and_source_revisions_rejected():
    record = qualified_record()
    record["sdk_version"] = "7.0.3"
    record["provider_commit"] = "not-an-immutable-pin"
    assert len(validate(record)) >= 2


def test_no_credentials_in_template_and_cli_fail_closed(tmp_path):
    path = tmp_path / "result.json"
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
    assert gate.main(["--template"]) == 0


@pytest.mark.parametrize("field", ["core_commit", "provider_commit"])
def test_complete_evidence_from_another_revision_is_not_accepted(field):
    record = qualified_record()
    record[field] = "c" * 40
    assert validate(record)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sender_profile_tag", "peer-A "),
        ("recipient_profile_tag", " "),
    ],
)
def test_noncanonical_profile_tags_are_not_independent_evidence(field, value):
    record = qualified_record()
    record[field] = value
    assert validate(record)


def test_unknown_fields_and_blank_evidence_are_rejected():
    record = qualified_record()
    record["unexpected"] = "do-not-echo"
    record["cases"][gate.CASES[0]]["evidence_ref"] = " "
    assert len(validate(record)) >= 2


def test_duplicate_json_fields_refuse_without_echoing_record(tmp_path, capsys):
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


def test_validation_requires_explicit_target_revisions(tmp_path):
    with pytest.raises(SystemExit) as exc:
        gate.main(["--validate", str(tmp_path / "not-read.json")])
    assert exc.value.code == 2
