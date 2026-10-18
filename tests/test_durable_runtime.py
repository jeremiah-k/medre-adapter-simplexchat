import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from medre.adapters.simplexchat.adapter import SimplexChatAdapter
from medre.adapters.simplexchat.state import ProfileLease
from medre.core.contracts.adapter import AdapterPermanentError

from conftest import NativeSession, attempt, item, rendered, wait_until


def test_exclusive_profile_namespace_survives_ownership_release(config):
    first = ProfileLease(config.database_prefix)
    namespace = first.namespace
    with pytest.raises(RuntimeError, match="owned"):
        ProfileLease(config.database_prefix)
    first.close()
    second = ProfileLease(config.database_prefix)
    assert second.namespace == namespace
    second.close()


async def test_baseline_scans_every_page_and_handles_nonmonotonic_native_ids(
    config, context
):
    ctx, checkpoints = context
    # Native timestamp order deliberately puts the largest ID outside the
    # latest page: it represents a late/backdated group message.
    rows = [item(500)] + [item(n) for n in range(1, 140)]
    session = NativeSession(config, rows)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        assert not ctx.admit_inbound.called
        assert checkpoints["simplexchat:1:group:1"].cursor == "initialized"
        assert len([call for call in session.calls if call[0] == "history"]) == 3
        session.incoming(item(501))
        await wait_until(lambda: ctx.admit_inbound.await_count == 1)
        assert ctx.admit_inbound.call_args.args[1] == "live"
        session.incoming(item(501))
        await wait_until(lambda: not session.events)
        assert ctx.admit_inbound.await_count == 1
    finally:
        await adapter.stop()


async def test_restart_recovers_backdated_items_without_repeating_live_admission(
    config, context
):
    ctx, checkpoints = context
    rows = [item(10)]
    first = NativeSession(config, rows)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: first)
    await adapter.start(ctx)
    first.incoming(item(12))
    await wait_until(lambda: ctx.admit_inbound.await_count == 1)
    await adapter.stop()
    rows.insert(0, item(13))  # Earlier native timestamp, higher database ID.
    second = NativeSession(config, rows)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: second)
    await adapter.start(ctx)
    try:
        assert ctx.admit_inbound.await_count == 2
        assert ctx.admit_inbound.call_args.args[
            0
        ].source_native_ref.native_message_id.endswith(":13")
        assert ctx.admit_inbound.call_args.args[1] == "recovered"
        assert checkpoints["simplexchat:1:group:1"].cursor == "initialized"
    finally:
        await adapter.stop()


async def test_failed_admission_retries_same_uuid_and_does_not_seal_live_ids(
    config, context
):
    ctx, checkpoints = context
    session = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        ctx.admit_inbound.side_effect = RuntimeError("storage unavailable")
        session.incoming(item(1))
        await wait_until(lambda: adapter.admission_failures > 0)
        original = ctx.admit_inbound.call_args.args[0].event_id
        assert checkpoints["simplexchat:1:group:1"].cursor == "initialized"
        ctx.admit_inbound.side_effect = None
        await wait_until(lambda: adapter.state == "healthy")
        assert ctx.admit_inbound.call_args.args[0].event_id == original
        assert adapter.inbox.contains("group:1", 1)
    finally:
        await adapter.stop()


async def test_recovery_admission_failure_does_not_advance_checkpoint(config, context):
    ctx, checkpoints = context
    # Establish the baseline and retain its inbox before simulating restart.
    initial = SimplexChatAdapter(
        config, session_factory=lambda _: NativeSession(config, [item(10)])
    )
    await initial.start(ctx)
    await initial.stop()
    ctx.admit_inbound.side_effect = RuntimeError("admission refused")
    session = NativeSession(config, [item(10), item(11)])
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    with pytest.raises(RuntimeError, match="admission refused"):
        await adapter.start(ctx)
    assert not session.started
    assert session.closed
    assert checkpoints["simplexchat:1:group:1"].cursor == "initialized"


async def test_confirmed_attempt_is_idempotent_and_provenance_is_immutable(
    config, context
):
    session = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    try:
        result = await adapter.deliver(rendered(attempt()))
        duplicate = await adapter.deliver(rendered(attempt()))
        assert result.native_message_id == duplicate.native_message_id
        assert result.confirmation_level == "local_queue"
        assert len(session.sent) == 1
        with pytest.raises(AdapterPermanentError):
            await adapter.deliver(rendered(replace(attempt(), source="retry")))
        with pytest.raises(AdapterPermanentError):
            await adapter.deliver(rendered())
    finally:
        await adapter.stop()


async def test_uncertain_native_handoff_blocks_resubmission_across_restart(
    config, context
):
    first = NativeSession(config)
    first.fail_send = True
    adapter = SimplexChatAdapter(config, session_factory=lambda _: first)
    await adapter.start(context[0])
    with pytest.raises(AdapterPermanentError):
        await adapter.deliver(rendered(attempt()))
    await adapter.stop()
    second = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: second)
    await adapter.start(context[0])
    try:
        for number in (1, 2):
            with pytest.raises(AdapterPermanentError):
                await adapter.deliver(rendered(attempt(number)))
        assert not second.sent
    finally:
        await adapter.stop()


async def test_malformed_live_event_fails_closed(config, context):
    session = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    session.events.append({"chatInfo": {}, "chatItem": None})
    session.wake.set()
    await wait_until(lambda: adapter.state == "failed")
    assert session.closed
    await adapter.stop()


async def test_new_attempt_cannot_repeat_a_previously_confirmed_outbox(config, context):
    session = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    try:
        await adapter.deliver(rendered(attempt()))
        with pytest.raises(AdapterPermanentError):
            await adapter.deliver(rendered(attempt(2)))
        assert len(session.sent) == 1
    finally:
        await adapter.stop()


async def test_checkpoint_with_missing_inbox_refuses_to_discard_history(
    config, context
):
    ctx, checkpoints = context
    checkpoints["simplexchat:1:group:1"] = SimpleNamespace(
        cursor="initialized",
        metadata_json=json.dumps(
            {"profile_id": "profile-uuid", "mode": "retained_ids_v1"}
        ),
    )
    session = NativeSession(config, [item(11)])
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    with pytest.raises(AdapterPermanentError, match="inbox differs"):
        await adapter.start(ctx)
    assert session.closed
    assert not ctx.admit_inbound.called


async def test_recovery_replays_missed_pages_oldest_first(config, context):
    ctx, _ = context
    baseline = SimplexChatAdapter(
        config, session_factory=lambda _: NativeSession(config, [item(1)])
    )
    await baseline.start(ctx)
    await baseline.stop()

    # Every page is sorted by timestamp but pages arrive newest first. IDs
    # need not be monotonic with timestamp (the oldest late item has ID 500).
    rows = [item(1), item(500)] + [item(n) for n in range(2, 132)]
    session = NativeSession(config, rows)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        ids = [
            int(call.args[0].source_native_ref.native_message_id.rsplit(":", 1)[-1])
            for call in ctx.admit_inbound.await_args_list
        ]
        assert ids == [500, *range(2, 132)]
        assert all(
            call.args[1] == "recovered" for call in ctx.admit_inbound.await_args_list
        )
    finally:
        await adapter.stop()


async def test_not_ready_is_retryable_and_does_not_reserve(config, context):
    from medre.core.contracts.adapter import AdapterSendError

    adapter = SimplexChatAdapter(
        config, session_factory=lambda _: NativeSession(config)
    )
    with pytest.raises(AdapterSendError, match="not available"):
        await adapter.deliver(rendered(attempt()))


async def test_unavailable_before_write_releases_handoff_reservation(config, context):
    from medre.adapters.simplexchat.session import SessionUnavailable
    from medre.core.contracts.adapter import AdapterSendError

    class PrewriteFailure(NativeSession):
        fail_before_write = True

        async def call(self, op, **kwargs):
            if op == "send" and self.fail_before_write:
                self.fail_before_write = False
                raise SessionUnavailable("SimpleX session is unavailable")
            return await super().call(op, **kwargs)

    session = PrewriteFailure(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    try:
        with pytest.raises(AdapterSendError, match="before send"):
            await adapter.deliver(rendered(attempt()))
        assert (
            adapter.ledger.db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]
            == 0
        )
        result = await adapter.deliver(rendered(attempt()))
        assert result.confirmation_level == "local_queue"
        assert len(session.sent) == 1
    finally:
        await adapter.stop()


def test_release_cannot_remove_confirmed_reservation(config):
    from medre.adapters.simplexchat.state import HandoffLedger

    from pathlib import Path

    ledger = HandoffLedger(Path(config.state_dir) / "handoffs.sqlite")
    try:
        assert ledger.reserve(attempt(), "group:1", "hello") == (True, None)
        ledger.accepted(attempt(), "native:1")
        ledger.release(attempt())
        assert ledger.reserve(attempt(), "group:1", "hello") == (False, "native:1")
    finally:
        ledger.close()


async def test_recovery_rereads_bounded_pages_in_oldest_first_order(config, context):
    ctx, _ = context
    session = NativeSession(config, [item(1)])
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    await adapter.stop()
    session = NativeSession(config, [item(n) for n in range(1, 150)])
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        observed = [
            int(call.args[0].source_native_ref.native_message_id.rsplit(":", 1)[-1])
            for call in ctx.admit_inbound.call_args_list
        ]
        assert observed == list(range(2, 150))
        pages = [args for op, args in session.calls if op == "history"]
        assert len(pages) == 6
        assert all(args["count"] == 64 for args in pages)
    finally:
        await adapter.stop()
