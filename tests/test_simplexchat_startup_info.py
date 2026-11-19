"""Startup-info provider: duck-typed emission of held SimpleX Chat facts.

Covers:
- ``startup_info`` is discovered duck-typed on the adapter object (real
  and fake).
- Facts come only from already-held state: the configured native user
  identity and the installed ``simplex-chat`` client distribution
  version read via ``importlib.metadata`` (never a source literal —
  the pin-hygiene guard keeps pins in pyproject only).
- The hook never initialises the native client: importing the Python
  ``simplex_chat`` package couples startup to a natively-downloaded
  library, so the provider only reads package metadata.
- Facts the adapter does not hold — server mode, server address, an
  operator address, the native server's version — are omitted, never
  probed.  Secret material (database keys, pairing) is never offered.
- Pure read: calling the hook mutates no adapter or session state and
  is safe on a worker thread, matching core's off-loop invocation.
- The configured user identity renders in full through core's
  redaction boundary.

Core-side collection/rendering symbols are imported in tests only; the
adapter source must not import them.  Configuration-only fakes — no
native process, no RPC, no library download.  See core
``docs/spec/adapter-startup-info.md``.
"""

from __future__ import annotations

import ast
import importlib.metadata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from medre.core.startup_info import (
    collect_startup_info,
    render_startup_info_block,
    startup_info_callable,
)

from medre.adapters.simplexchat import startup_info as startup_info_module
from medre.adapters.simplexchat.adapter import SimplexChatAdapter
from medre.adapters.simplexchat.config import SimplexChatConfig
from medre.adapters.simplexchat.fake import FakeSimplexChatAdapter

_EMITTED_AT = "2026-11-19T00:00:00+00:00"

# Synthetic client version for deterministically exercising the held
# version fact.  Not a pin value; the repo pins live in pyproject only.
_SYNTHETIC_CLIENT_VERSION = "1.2.3"


# -- Helpers ---------------------------------------------------------------


def _make_config(**overrides: Any) -> SimplexChatConfig:
    defaults: dict[str, Any] = {
        "chats": ("group:1",),
        "relay_prefix": "",
        "database_key": "test-only-secret-not-a-real-key",
    }
    defaults.update(overrides)
    return SimplexChatConfig("sx", **defaults)


def _account_row(user_id: int = 1) -> list[dict[str, Any]]:
    return [{"name": "user_id", "value": user_id, "label": "user"}]


def _installed_client_version() -> str | None:
    """The real environment's answer, computed dynamically (no literals)."""
    try:
        return importlib.metadata.version("simplex-chat")
    except importlib.metadata.PackageNotFoundError:
        return None


def _expected_rows(user_id: int = 1) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = [_account_row(user_id)]
    client_version = _installed_client_version()
    if client_version is not None:
        rows.append(
            [
                {
                    "name": "client_version",
                    "value": client_version,
                    "label": "client",
                }
            ]
        )
    return {"rows": rows}


# -- Duck-typed discovery ---------------------------------------------------


def test_startup_info_symbol_is_discoverable() -> None:
    """Core's getattr discovery finds a callable on the adapter object."""
    adapter = SimplexChatAdapter(_make_config())
    discovered = startup_info_callable(adapter)
    assert discovered is not None
    assert callable(discovered)
    assert discovered.__name__ == "startup_info"


def test_fake_adapter_exposes_discoverable_startup_info() -> None:
    """The fake mirrors the real adapter's duck-typed seam."""
    fake = FakeSimplexChatAdapter("sx", _make_config())
    discovered = startup_info_callable(fake)
    assert discovered is not None
    assert callable(discovered)
    assert discovered.__name__ == "startup_info"


# -- Held-fact formatting ---------------------------------------------------


def test_configured_user_identity_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured native user ID is the account identity row."""
    monkeypatch.setattr(startup_info_module, "_installed_client_version", lambda: None)
    adapter = SimplexChatAdapter(_make_config(user_id=7))
    assert adapter.startup_info() == {"rows": [_account_row(7)]}


def test_client_version_fact_follows_installed_metadata() -> None:
    """The version fact tracks ``importlib.metadata`` exactly.

    When the client distribution is installed the fact equals the
    queried metadata value; when it is absent the fact is omitted.
    Either way the comparison is against a computed value, never a
    literal pin.
    """
    installed = _installed_client_version()
    adapter = SimplexChatAdapter(_make_config())
    rows = adapter.startup_info()["rows"]
    values = [
        fact["value"]
        for row in rows
        for fact in row
        if fact["name"] == "client_version"
    ]
    if installed is None:
        assert values == []
    else:
        assert values == [installed]


def test_server_and_address_facts_are_never_probed() -> None:
    """Server mode/address and operator address are only reportable if
    the adapter holds them after native init; it holds none, so none
    may appear — and they are never probed from the native client."""
    adapter = SimplexChatAdapter(_make_config())
    rows = adapter.startup_info()["rows"]
    names = [fact["name"] for row in rows for fact in row]
    assert not any(
        token in name
        for name in names
        for token in ("server", "address", "mode", "smp", "operator")
    )


def test_secret_material_is_never_offered() -> None:
    """Database keys and pairing material never appear as facts."""
    adapter = SimplexChatAdapter(_make_config())
    rows = adapter.startup_info()["rows"]
    names = [fact["name"] for row in rows for fact in row]
    values = [str(fact["value"]) for row in rows for fact in row]
    assert not any(
        token in name for name in names for token in ("key", "secret", "pairing")
    )
    assert "test-only-secret-not-a-real-key" not in " ".join(values)


def test_fake_adapter_reports_configured_facts_only() -> None:
    """The fake shapes the same configured facts through the same seam."""
    fake = FakeSimplexChatAdapter("sx", _make_config())
    assert fake.startup_info() == SimplexChatAdapter(_make_config()).startup_info()


# -- Purity and thread safety -----------------------------------------------


def test_startup_info_is_pure_read() -> None:
    """Reading facts neither mutates held state nor drifts between calls."""
    adapter = SimplexChatAdapter(_make_config())
    before = adapter.diagnostics()
    first = adapter.startup_info()
    second = adapter.startup_info()
    assert first == second
    assert adapter.diagnostics() == before
    assert adapter.session is None
    assert adapter.state == "unknown"


def test_startup_info_snapshot_is_safe_from_worker_threads() -> None:
    """Core runs synchronous providers off-loop; the read must be stable."""
    adapter = SimplexChatAdapter(_make_config())
    with ThreadPoolExecutor(max_workers=4) as pool:
        payloads = list(pool.map(lambda _: adapter.startup_info(), range(32)))
    assert all(payload == payloads[0] for payload in payloads)
    assert payloads[0] == _expected_rows()


def test_provider_module_imports_nothing_from_core_or_native_client() -> None:
    """Plain data only: no medre.core coupling, no simplex_chat import.

    Importing the Python client would couple the provider to a
    natively-downloaded library; package metadata is read instead.
    """
    source = Path(startup_info_module.__file__ or "").read_text(encoding="utf-8")
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            offenders.extend(
                alias.name
                for alias in node.names
                if alias.name == "medre.core"
                or alias.name.startswith("medre.core.")
                or alias.name == "simplex_chat"
                or alias.name.startswith("simplex_chat.")
            )
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ""
            if (
                module == "medre.core"
                or module.startswith("medre.core.")
                or module == "simplex_chat"
                or module.startswith("simplex_chat.")
            ):
                offenders.append(module)
    assert offenders == []


# ---------------------------------------------------------------------------
# Core boundary: collection, rendering
# ---------------------------------------------------------------------------


async def test_adapter_emits_held_facts_through_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Core's supervised collection preserves rows and renders the
    configured user identity in full."""
    monkeypatch.setattr(startup_info_module, "_installed_client_version", lambda: None)
    adapter = SimplexChatAdapter(_make_config())
    info = await collect_startup_info(
        "sx-1", "simplexchat", adapter, emitted_at=_EMITTED_AT
    )
    assert info is not None
    assert [(f.name, f.row, f.label) for f in info.fields] == [
        ("user_id", 0, "user"),
    ]
    assert info.fields[0].value == "1"  # core renders scalars as display strings
    assert render_startup_info_block("simplexchat.sx-1  ready", info) == (
        "simplexchat.sx-1  ready\n" "  user 1"
    )


async def test_core_collection_keeps_version_fact_when_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held client version renders on its own row through core."""
    monkeypatch.setattr(
        startup_info_module,
        "_installed_client_version",
        lambda: _SYNTHETIC_CLIENT_VERSION,
    )
    adapter = SimplexChatAdapter(_make_config())
    info = await collect_startup_info(
        "sx-1", "simplexchat", adapter, emitted_at=_EMITTED_AT
    )
    assert info is not None
    assert [(f.name, f.row, f.label) for f in info.fields] == [
        ("user_id", 0, "user"),
        ("client_version", 1, "client"),
    ]
    assert info.fields[1].value == _SYNTHETIC_CLIENT_VERSION
    assert render_startup_info_block("simplexchat.sx-1  ready", info) == (
        "simplexchat.sx-1  ready\n" "  user 1\n" "  client 1.2.3"
    )
