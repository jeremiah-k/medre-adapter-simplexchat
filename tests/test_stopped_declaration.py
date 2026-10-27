"""SimpleX chat stopped-state declaration truthfulness.

The declaration module is provider knowledge: it maps one configured native
profile to the exact files a stopped capture must select and reuses this
adapter's own flock guard for local exclusion. Synthetic state only; no
native process, profile, or SDK import is involved.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from medre.adapters.simplexchat.plugin import adapter_spec
from medre.adapters.simplexchat.stopped_declaration import declaration
from medre.maintenance.stopped_declare import (
    DeclaredFile,
    ExclusionRefused,
    LocalExclusion,
    declaration_from_spec,
    declared,
    plan_from_coverages,
)
from medre.maintenance.stopped_snapshot import create_stopped_snapshot
from medre.maintenance.stopped_inventory import StoppedInventory


def _write(path: Path, payload: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _sqlite_database(path: Path, table: str = "kept") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"CREATE TABLE {table}(value TEXT)")
        connection.execute(f"INSERT INTO {table} VALUES('committed')")


def _settings(root: Path, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "database_prefix": str(root / "profile"),
        "state_dir": str(root),
        "database_key": "",
        "native_cache_dir": str(root / "cache"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_spec_references_the_declaration_module():
    reference = adapter_spec().stopped_state_declaration
    assert reference is not None
    assert reference.module == "medre.adapters.simplexchat.stopped_declaration"
    assert reference.name == "declaration"
    assert declaration().provider == "simplexchat"


def test_core_resolution_resolves_the_referenced_factory(tmp_path):
    resolved = declaration_from_spec(adapter_spec())
    assert resolved.provider == "simplexchat"
    coverage = resolved.coverage(_settings(tmp_path), adapter_id="main")
    assert coverage.state_class == "file_backed"
    assert len(coverage.files) == 6


def test_profile_databases_ledgers_and_guard_are_required(tmp_path):
    coverage = declaration().coverage(_settings(tmp_path), adapter_id="main")
    assert coverage.state_class == "file_backed"
    required = {item.path.name: item for item in coverage.files}
    assert set(required) == {
        "handoffs.sqlite",
        "inbox.sqlite",
        "profile.medre-owner.sqlite",
        "profile.medre-owner.lock",
        "profile_agent.db",
        "profile_chat.db",
    }
    assert required["handoffs.sqlite"].role == "adapter_ledger"
    assert required["profile_chat.db"].role == "native_identity"
    assert required["profile_agent.db"].format == "sqlite"
    assert required["profile.medre-owner.lock"].format == "opaque"
    assert any("live lock" in note for note in coverage.notes)


def test_cache_directory_is_never_selected(tmp_path):
    coverage = declaration().coverage(_settings(tmp_path), adapter_id="main")
    assert all("cache" not in item.path.name for item in coverage.files)
    assert any("disposable" in note for note in coverage.notes)


def test_missing_configuration_is_unsupported_not_empty(tmp_path):
    coverage = declaration().coverage(
        _settings(tmp_path, database_prefix=""), adapter_id="main"
    )
    assert coverage.state_class == "unsupported"
    assert coverage.files == ()


def test_encrypted_profile_requires_provider_export(tmp_path):
    coverage = declaration().coverage(
        _settings(tmp_path, database_key="passphrase"), adapter_id="main"
    )
    assert coverage.state_class == "unsupported"
    assert coverage.files == ()
    assert any("export" in note for note in coverage.notes)


def test_exclusion_uses_the_adapter_guard_and_refuses_when_held(tmp_path):
    prefix = tmp_path / "profile"
    guard = _write(Path(str(prefix) + ".medre-owner.lock"))
    exclusion = declaration().local_exclusion(_settings(tmp_path), adapter_id="main")
    assert exclusion.method == "flock"
    assert exclusion.scope == "local_only"
    competitor = LocalExclusion("simplexchat", note="probe")
    with pytest.raises(ExclusionRefused, match="held"):
        competitor.retain_flock(guard, purpose="guard")
    exclusion.close()
    # Released; the guard inode stays in place for the next owner.
    assert guard.is_file()


def test_exclusion_refuses_to_seize_an_absent_guard(tmp_path):
    with pytest.raises(ExclusionRefused, match="never owned"):
        declaration().local_exclusion(_settings(tmp_path), adapter_id="main")


def _stopped_state(tmp_path: Path) -> tuple[Path, SimpleNamespace]:
    root = tmp_path / "state"
    settings = _settings(root)
    _sqlite_database(root / "medre.sqlite", "events")
    _sqlite_database(Path(str(settings.database_prefix) + "_agent.db"), "agent")
    _sqlite_database(Path(str(settings.database_prefix) + "_chat.db"), "chat")
    _sqlite_database(root / "handoffs.sqlite", "handoffs")
    _sqlite_database(root / "inbox.sqlite", "inbox")
    _sqlite_database(Path(str(settings.database_prefix) + ".medre-owner.sqlite"))
    _write(Path(str(settings.database_prefix) + ".medre-owner.lock"), b"guard")
    return root, settings


def test_plan_and_inventory_round_trip_through_core_machinery(tmp_path):
    root, settings = _stopped_state(tmp_path)
    coverage = declaration().coverage(settings, adapter_id="main")
    core = declared(
        provider="core",
        adapter_id="core",
        files=(DeclaredFile(root / "medre.sqlite", "core_sqlite", "sqlite"),),
    )
    built = plan_from_coverages(root, [core, coverage])
    document = built.inventory_document()
    assert sorted(document["providers"]) == ["core", "simplexchat"]
    assert len(document["files"]) == 7
    filename = tmp_path / "inventory.json"
    built.write_inventory(filename)
    loaded = StoppedInventory.load(filename)
    assert loaded.providers == ("core", "simplexchat")


def test_retained_exclusion_extends_over_library_capture(tmp_path):
    """flock retention must not block the SQLite backup of selected files."""
    root, settings = _stopped_state(tmp_path)
    coverage = declaration().coverage(settings, adapter_id="main")
    core = declared(
        provider="core",
        adapter_id="core",
        files=(DeclaredFile(root / "medre.sqlite", "core_sqlite", "sqlite"),),
    )
    built = plan_from_coverages(root, [core, coverage])
    snapshot = tmp_path / "snapshot"
    guard = Path(str(settings.database_prefix) + ".medre-owner.lock")
    with exclusion_retained(settings):
        # A held flock does not interfere with the SQLite backup protocol.
        create_stopped_snapshot(root, snapshot, built.entries, runtime_stopped=True)
        competitor = LocalExclusion("simplexchat", note="probe")
        with pytest.raises(ExclusionRefused, match="held"):
            competitor.retain_flock(guard, purpose="guard")
    assert (snapshot / "manifest.json").is_file()
    with closing(sqlite3.connect(snapshot / "files/profile_chat.db")) as connection:
        assert connection.execute("SELECT value FROM chat").fetchone() == ("committed",)


def exclusion_retained(settings: SimpleNamespace):
    from contextlib import contextmanager

    @contextmanager
    def _hold():
        exclusion = declaration().local_exclusion(settings, adapter_id="main")
        try:
            yield exclusion
        finally:
            exclusion.close()

    return _hold()


def test_encrypted_profile_cannot_be_omitted_from_a_successful_capture_plan(tmp_path):
    """An encrypted profile cannot silently discard identity and handoff journals."""
    from medre.maintenance.stopped_declare import StoppedPlanError

    coverage = declaration().coverage(
        _settings(tmp_path, database_key="secret"), adapter_id="main"
    )
    core = declared(
        provider="core",
        adapter_id="core",
        files=(DeclaredFile(tmp_path / "core.sqlite", "core_sqlite", "sqlite"),),
    )
    with pytest.raises(StoppedPlanError, match="no stopped-state declaration"):
        plan_from_coverages(tmp_path, [core, coverage])
