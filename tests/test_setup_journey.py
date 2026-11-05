"""Typed SimpleX provisioning journey: verbatim commands, true evidence levels.

The declaration is informational only. Cores with the
provider-setup-instructions seam render it; older cores construct the spec
exactly as before. No SDK import, native profile, or network occurs on the
declaration path.
"""

from __future__ import annotations

import dataclasses
import re
import shlex
import subprocess
import sys

import pytest

import medre.adapter_spec as core_adapter_spec
from medre.adapters.simplexchat import cli
from medre.adapters.simplexchat.plugin import adapter_spec
from medre.adapters.simplexchat.setup_journey import (
    STEPS,
    SUMMARY,
    TITLE,
    journey,
)

HAS_TYPED_SEAM = hasattr(core_adapter_spec, "NativeSetupInstructions")
TYPED_SEAM_REASON = "core provides no typed native setup instructions seam"

# The README journey order: prerequisites stay in the summary because they
# carry no command; every declared step is a verbatim provisioning command.
EXPECTED_COMMANDS = tuple(step[0] for step in STEPS)

EXPECTED_STEP_MARKERS = {
    "init": ("display name", "proves nothing about connectivity"),
    "join": ("does not establish group membership", "independently witnessed"),
    "groups": ("does not prove group membership",),
    "accept-group": ("membership state",),
    "connect": ("does not establish group membership",),
    "create-group": ("not witnessed here",),
    "contacts": ("independently witnessed",),
}

EXPECTED_PARSE_FIELDS = {
    "init": {"name": "MEDRE bridge"},
    "join": {"link_stdin": False, "wait_seconds": 60.0},
    "groups": {"json": True},
    "accept-group": {"group_id": 7},
    "connect": {"wait_seconds": 60.0},
    "create-group": {"name": "Bridge room"},
    "contacts": {},
}


def test_spec_declares_the_typed_journey():
    """The spec carries the typed journey exactly when core provides the seam."""
    spec = adapter_spec()
    if not HAS_TYPED_SEAM:
        assert not hasattr(spec, "native_setup_instructions")
        assert journey() is None
        pytest.skip(TYPED_SEAM_REASON)
    instructions = spec.native_setup_instructions
    assert instructions is not None
    # The declaration is deterministic: a fresh build equals the declared one.
    assert instructions == journey()
    assert isinstance(instructions, core_adapter_spec.NativeSetupInstructions)
    assert instructions.title.strip()
    assert instructions.summary.strip()
    assert type(instructions.steps) is tuple
    assert instructions.steps
    for step in instructions.steps:
        assert isinstance(step, core_adapter_spec.NativeSetupStep)
        assert step.command.strip()
        assert step.purpose.strip()
        assert step.evidence_note.strip()


@pytest.mark.skipif(not HAS_TYPED_SEAM, reason=TYPED_SEAM_REASON)
def test_journey_models_match_the_core_contract():
    """Declared models are the core-owned frozen, slotted value types."""
    for model in (
        core_adapter_spec.NativeSetupInstructions,
        core_adapter_spec.NativeSetupStep,
    ):
        assert dataclasses.is_dataclass(model)
        assert model.__dataclass_params__.frozen
        assert model.__dataclass_params__.slots
    assert [f.name for f in dataclasses.fields(core_adapter_spec.NativeSetupStep)] == [
        "command",
        "purpose",
        "evidence_note",
    ]
    assert [
        f.name for f in dataclasses.fields(core_adapter_spec.NativeSetupInstructions)
    ] == ["title", "summary", "steps"]
    spec_fields = {f.name: f for f in dataclasses.fields(core_adapter_spec.AdapterSpec)}
    assert spec_fields["native_setup_instructions"].default is None
    assert spec_fields["native_setup_fields"].default == ()


@pytest.mark.skipif(not HAS_TYPED_SEAM, reason=TYPED_SEAM_REASON)
def test_step_order_matches_the_readme_journey():
    """Declared steps follow the staged README journey in order."""
    commands = tuple(step.command for step in journey().steps)
    assert commands == (
        "medre adapter simplexchat init --adapter sx --name 'MEDRE bridge'",
        "medre adapter simplexchat join --adapter sx",
        "medre adapter simplexchat groups --adapter sx --json",
        "medre adapter simplexchat accept-group --adapter sx --group-id ID",
        "medre adapter simplexchat connect --adapter sx",
        "medre adapter simplexchat create-group --adapter sx --name 'Bridge room'",
        "medre adapter simplexchat contacts --adapter sx",
    )


@pytest.mark.skipif(not HAS_TYPED_SEAM, reason=TYPED_SEAM_REASON)
@pytest.mark.parametrize("command", EXPECTED_COMMANDS)
def test_command_is_verbatim_against_shipped_cli_contributions(command):
    """Every declared command parses against the shipped provider parser."""
    tokens = shlex.split(command)
    assert tokens[:3] == ["medre", "adapter", "simplexchat"]
    # The accept-group placeholder names the local ID reported by groups.
    argv = ["7" if token == "ID" else token for token in tokens[3:]]
    parsed = cli.parser().parse_args(argv)
    assert parsed.command == tokens[3]
    assert parsed.adapter == "sx"
    for name, value in EXPECTED_PARSE_FIELDS[parsed.command].items():
        assert getattr(parsed, name) == value


@pytest.mark.skipif(not HAS_TYPED_SEAM, reason=TYPED_SEAM_REASON)
def test_evidence_notes_state_true_levels():
    """Notes prove only local outcomes and deny relay, delivery, and reads."""
    for command, purpose, note in STEPS:
        subcommand = shlex.split(command)[3]
        assert purpose.strip()
        # Universal provisioning discipline at its true level.
        assert "relay acceptance, delivery, or read evidence" in note
        assert "grants no send authority" in note
        for marker in EXPECTED_STEP_MARKERS[subcommand]:
            assert marker in note, (subcommand, marker)


@pytest.mark.skipif(not HAS_TYPED_SEAM, reason=TYPED_SEAM_REASON)
def test_declaration_carries_no_secrets_paths_or_timestamps():
    """Deterministic declarations exclude secrets, machine paths, and clocks."""
    declared = (TITLE, SUMMARY, *(fragment for step in STEPS for fragment in step))
    for text in declared:
        assert "/" not in text, text
        assert "MEDRE_ADAPTER__" not in text
        assert not re.search(r"\d{4}-\d{2}", text), text


def test_unavailable_symbol_constructs_exactly_today_spec(monkeypatch):
    """A core without the seam builds the identical spec without the kwarg."""
    baseline = adapter_spec()
    monkeypatch.delattr(core_adapter_spec, "NativeSetupInstructions", raising=False)
    monkeypatch.delattr(core_adapter_spec, "NativeSetupStep", raising=False)
    assert journey() is None
    fallback = adapter_spec()
    for field in dataclasses.fields(type(baseline)):
        if field.name == "native_setup_instructions":
            continue
        assert getattr(fallback, field.name) == getattr(baseline, field.name)
    if HAS_TYPED_SEAM:
        assert fallback.native_setup_instructions is None
        assert dataclasses.replace(baseline, native_setup_instructions=None) == fallback
    else:
        assert not hasattr(fallback, "native_setup_instructions")


def test_spec_keeps_the_form_contract_untouched():
    """Provisioning stays operator-owned: no offline form is declared."""
    spec = adapter_spec()
    assert spec.native_setup_fields == ()
    if HAS_TYPED_SEAM:
        # The provider qualifies for the informational journey render only.
        assert spec.native_setup_instructions is not None


def test_declaration_path_never_imports_the_sdk():
    """Declaring the journey imports no native SDK, RPC client, or worker."""
    script = """
import importlib.abc
import sys
class BlockSdk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'deltachat_rpc_client', 'deltachat_rpc_server', 'simplex_chat'}:
            raise AssertionError('SDK imported for declaration: ' + fullname)
sys.meta_path.insert(0, BlockSdk())
from medre.adapters.simplexchat.plugin import adapter_spec
spec = adapter_spec()
assert spec.transport == 'simplexchat'
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_legacy_spec_without_instructions_field_still_discovers_provider(monkeypatch):
    from medre.adapters.simplexchat import plugin

    class LegacyAdapterSpec:
        def __init__(self, **fields):
            assert "native_setup_instructions" not in fields
            self.transport = fields["transport"]

    monkeypatch.setattr(plugin, "AdapterSpec", LegacyAdapterSpec)
    assert plugin.adapter_spec().transport == "simplexchat"
