"""Verify that SimpleX startup facts are limited to the installed client SDK."""

import ast
import importlib.metadata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from medre.core.startup_info import (
    CACHE_FILENAME,
    collect_startup_info,
    read_cache_file,
    render_startup_info_block,
    startup_info_callable,
    write_cache_file,
)

from medre.adapters.simplexchat import startup_info as startup_info_module
from medre.adapters.simplexchat.adapter import SimplexChatAdapter
from medre.adapters.simplexchat.config import SimplexChatConfig
from medre.adapters.simplexchat.fake import FakeSimplexChatAdapter

_VERSION = "1.2.3"
_ROWS = [[{"name": "client_version", "value": _VERSION, "label": "client SDK"}]]
_EMITTED_AT = "2026-11-19T00:00:00+00:00"


def _config(user_id: int = 7) -> SimplexChatConfig:
    return SimplexChatConfig(
        "sx", user_id=user_id, chats=("group:1",), database_key="fixture-key"
    )


@pytest.fixture
def client_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def version(name: str) -> str:
        assert name == "simplex-chat"
        return _VERSION

    monkeypatch.setattr(startup_info_module.importlib.metadata, "version", version)


def test_only_sdk_version_is_offered_for_real_and_fake_profiles(client_version) -> None:
    for config in (_config(7), _config(11)):
        real = SimplexChatAdapter(config)
        fake = FakeSimplexChatAdapter("sx", config)
        assert startup_info_callable(real) is not None
        assert startup_info_callable(fake) is not None
        assert real.startup_info() == fake.startup_info() == {"rows": _ROWS}
        assert real.session is None


async def test_missing_client_distribution_has_no_startup_facts(monkeypatch) -> None:
    def version(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(startup_info_module.importlib.metadata, "version", version)
    adapter = SimplexChatAdapter(_config())
    assert (
        await collect_startup_info("sx", "simplexchat", adapter, emitted_at=_EMITTED_AT)
        is None
    )


def test_startup_info_is_stable_off_loop(client_version) -> None:
    adapter = SimplexChatAdapter(_config())
    before = adapter.diagnostics()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: adapter.startup_info(), range(16)))
    assert results == [{"rows": _ROWS}] * 16
    assert adapter.diagnostics() == before
    assert adapter.session is None


def test_provider_imports_neither_core_nor_native_client() -> None:
    source = Path(startup_info_module.__file__).read_text(encoding="utf-8")
    modules: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert not any(
        name == prefix or name.startswith(prefix + ".")
        for name in modules
        for prefix in ("medre.core", "simplex_chat")
    )


async def test_sdk_fact_renders_and_round_trips_through_cache(
    client_version, tmp_path: Path
) -> None:
    adapter = SimplexChatAdapter(_config())
    info = await collect_startup_info(
        "sx", "simplexchat", adapter, emitted_at=_EMITTED_AT
    )
    assert info is not None
    assert [(fact.name, fact.value) for fact in info.fields] == [
        ("client_version", _VERSION)
    ]
    assert render_startup_info_block("simplexchat.sx  ready", info) == (
        "simplexchat.sx  ready\n  client SDK 1.2.3"
    )
    cache = tmp_path / CACHE_FILENAME
    write_cache_file(cache, info)
    cached = read_cache_file(cache, adapter_id="sx", transport="simplexchat")
    assert cached == info.with_cached_flags(True)
