"""Unproven native termination retains exclusive profile ownership."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from medre.adapters.simplexchat.adapter import SimplexChatAdapter
from medre.adapters.simplexchat.session import (
    SessionError,
    SessionUnavailable,
    SimplexChatSession,
)
from medre.adapters.simplexchat.state import ProfileLease


def controlled_session(config):
    """A controlled process exposes shutdown boundaries without a native SDK."""
    session = SimplexChatSession(config)
    session.lease = ProfileLease(config.database_prefix)
    process = SimpleNamespace(
        returncode=None,
        stdin=SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock()),
        wait=AsyncMock(side_effect=TimeoutError),
    )
    session.process = process
    return session, process


async def test_failed_stop_fences_calls_and_retains_lease_for_retry(
    config, monkeypatch
):
    """A timeout cannot authorize a competing native profile owner or another call."""
    session, process = controlled_session(config)
    monkeypatch.setattr(session, "_kill_owned", lambda: None)
    try:
        with pytest.raises(TimeoutError):
            await session.stop()
        assert session.failed and not session.closed
        with pytest.raises(RuntimeError, match="owned"):
            ProfileLease(config.database_prefix)
        writes = process.stdin.write.call_count
        with pytest.raises(SessionUnavailable):
            await session.call("groups")
        assert process.stdin.write.call_count == writes
    finally:
        process.returncode = 0
        await session.stop()
    assert session.closed and session.lease is None
    ProfileLease(config.database_prefix).close()


async def test_cancelled_reaping_keeps_profile_fenced(config, monkeypatch):
    """Cancellation of cleanup cannot make an unconfirmed native owner disappear."""
    session, process = controlled_session(config)
    process.wait.side_effect = asyncio.CancelledError
    monkeypatch.setattr(session, "_kill_owned", lambda: None)
    try:
        with pytest.raises(asyncio.CancelledError):
            await session.stop()
        assert session.failed and session.lease is not None
        with pytest.raises(RuntimeError, match="owned"):
            ProfileLease(config.database_prefix)
    finally:
        process.returncode = 0
        await session.stop()


async def test_concurrent_and_repeated_stop_do_not_signal_stale_process(
    config, monkeypatch
):
    """One shutdown owner performs cleanup; completed sessions retain no PID authority."""
    session, process = controlled_session(config)
    entered, release = asyncio.Event(), asyncio.Event()
    kills = Mock()
    monkeypatch.setattr(session, "_kill_owned", kills)

    async def wait():
        entered.set()
        await release.wait()
        process.returncode = 0
        return 0

    process.wait = wait
    first = asyncio.create_task(session.stop())
    await entered.wait()
    second = asyncio.create_task(session.stop())
    release.set()
    await asyncio.gather(first, second)
    assert session.closed and session.lease is None
    assert process.stdin.write.call_count == 1
    kills.assert_called_once()
    await session.stop()
    kills.assert_called_once()


async def test_adapter_preserves_failed_native_owner_for_cleanup_retry(config):
    """Failed shutdown remains visible and prevents replacing the owned session."""
    adapter = SimplexChatAdapter(config)
    session = SimpleNamespace(
        stop=AsyncMock(side_effect=SessionError("unreaped worker"))
    )
    adapter.session = session
    adapter.state = "healthy"
    with pytest.raises(SessionError, match="unreaped"):
        await adapter.stop()
    assert adapter.session is session
    assert (await adapter.health_check()).health == "failed"
    session.stop.side_effect = None
    await adapter.stop()
    assert adapter.session is None


async def test_cleanup_retry_after_lease_error_does_not_signal_reaped_process(
    config, monkeypatch
):
    """A bookkeeping failure cannot restore authority to signal a reaped native PID."""
    session, process = controlled_session(config)
    process.returncode = 0
    lease = session.lease
    session.lease = SimpleNamespace(
        close=Mock(side_effect=RuntimeError("lease close failed"))
    )
    kills = Mock()
    monkeypatch.setattr(session, "_kill_owned", kills)
    try:
        with pytest.raises(RuntimeError, match="lease close"):
            await session.stop()
        assert session._native_reaped and not session.closed
        session.lease = lease
        await session.stop()
        assert session.closed
        kills.assert_called_once()
    finally:
        if lease is not None:
            lease.close()
