"""Profile health observations preserve local scope and the native ownership guard."""

import asyncio

import pytest

from medre.adapters.simplexchat.adapter import SimplexChatAdapter
from medre.adapters.simplexchat.session import SessionError
from medre.core.contracts.adapter import AdapterSendError
from conftest import NativeSession, item, rendered, wait_until


async def test_admission_failure_and_recovery_publish_without_probe(config, context):
    """A retained native event recovers only after its durable admission succeeds."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    session = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        assert reports[-1].health == "healthy"
        ctx.admit_inbound.side_effect = RuntimeError("storage unavailable")
        session.incoming(item(1))
        await wait_until(lambda: reports[-1].health == "degraded")
        ctx.admit_inbound.side_effect = None
        session.wake.set()
        await wait_until(lambda: reports[-1].health == "healthy")
        assert adapter.diagnostics()["health_scope"] == "native_profile"
        assert adapter.diagnostics()["peer_reachability"] == "not_inferred"
    finally:
        await adapter.stop()


async def test_worker_failure_is_published_before_native_retirement(config, context):
    """Native failure reports health without claiming that ownership was released."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    session = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    original = session.call

    async def failed(op, **kwargs):
        """Refuse local profile access after startup succeeds."""
        if op == "events":
            raise SessionError("native worker unavailable")
        return await original(op, **kwargs)

    try:
        session.call = failed
        session.wake.set()
        await wait_until(lambda: reports[-1].health == "failed")
    finally:
        await adapter.stop()


async def test_retired_loop_and_observer_errors_are_contained(config, context):
    """Failed diagnostic consumers retry at a native observation; retired loops stay fenced."""
    ctx, _ = context
    reports = []

    def refused(info):
        """Model a temporary optional observer outage."""
        raise RuntimeError("observer unavailable")

    ctx.report_health = refused
    adapter = SimplexChatAdapter(config, session_factory=NativeSession)
    await adapter.start(ctx)
    epoch = adapter._health_epoch
    ctx.report_health = reports.append
    adapter.session.wake.set()
    await wait_until(lambda: bool(reports))
    await adapter.stop()
    await adapter.start(ctx)
    try:
        count = len(reports)
        adapter._set_state("failed", epoch=epoch)
        assert adapter.state == "healthy"
        assert len(reports) == count
    finally:
        await adapter.stop()


async def test_owner_cancellation_before_first_execution_reports_failed(
    config, context
):
    """A task cancelled before its coroutine starts still publishes failed health."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    adapter = SimplexChatAdapter(config, session_factory=NativeSession)
    await adapter.start(ctx)
    try:
        task = adapter.task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await wait_until(lambda: reports[-1].health == "failed")
    finally:
        await adapter.stop()


async def test_shutdown_owned_cancellation_does_not_report_failed(config, context):
    """The stop fence retires observers before cancelling the native-owner task."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    adapter = SimplexChatAdapter(config, session_factory=NativeSession)
    await adapter.start(ctx)
    await adapter.stop()
    assert all(info.health != "failed" for info in reports)


async def test_failed_start_propagates_and_retires_native_reporting(config, context):
    """The lifecycle owner receives startup failure without admitting a native reporting lifetime."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    session = NativeSession(config)

    async def failed_start():
        """Refuse native startup before local profile ownership settles."""
        raise SessionError("native startup unavailable")

    session.start = failed_start
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    with pytest.raises(SessionError):
        await adapter.start(ctx)
    assert reports == []
    assert (await adapter.health_check()).health == "failed"
    assert not adapter._health_active
    assert session.closed


async def test_failed_shutdown_retains_failure_without_reopening_callbacks(
    config, context
):
    """Failed native cleanup stays fenced; its exception belongs to the lifecycle owner."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    session = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    epoch = adapter._health_epoch
    original = session.stop

    async def failed_stop():
        """Retain native ownership when cleanup cannot be confirmed."""
        raise SessionError("native cleanup unavailable")

    session.stop = failed_stop
    try:
        count = len(reports)
        with pytest.raises(SessionError):
            await adapter.stop()
        assert len(reports) == count
        assert (await adapter.health_check()).health == "failed"
        assert not adapter._health_active
        adapter._set_state("healthy", epoch=epoch)
        assert adapter.state == "failed"
        with pytest.raises(AdapterSendError):
            await adapter.deliver(rendered())
        assert session.sent == []
    finally:
        session.stop = original
        await adapter.stop()
