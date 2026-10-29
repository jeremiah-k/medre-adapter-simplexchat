"""Native SimpleX quote reservations are immutable across retries and restarts."""

import json

import pytest

from conftest import NativeSession, attempt, rendered
from medre.adapters.simplexchat.adapter import SimplexChatAdapter
from medre.core.contracts.adapter import AdapterPermanentError


def quote_payload(ref, *, fallback="[quoted] previous\nRendered message"):
    return {
        "text": "Rendered message",
        "chat_type": "group",
        "chat_id": 1,
        "reply_native_ref": ref,
        "reply_fallback_text": fallback,
    }


async def test_native_reply_is_sealed_in_handoff_and_reused_once(config, context):
    session = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    try:
        ref = adapter.codec.reference("group:1", 19)
        result = rendered(attempt(), payload=quote_payload(ref))
        first = await adapter.deliver(result)
        second = await adapter.deliver(result)
        assert first.native_message_id == second.native_message_id
        assert len(session.sent) == 1
        assert session.sent[0]["quoted_item_id"] == 19
        request = json.loads(
            adapter.ledger.db.execute("SELECT request FROM handoffs").fetchone()[0]
        )
        assert request[3:] == [ref, quote_payload(ref)["reply_fallback_text"]]
        different = adapter.codec.reference("group:1", 20)
        with pytest.raises(AdapterPermanentError):
            await adapter.deliver(rendered(attempt(), payload=quote_payload(different)))
        with pytest.raises(AdapterPermanentError):
            await adapter.deliver(
                rendered(
                    attempt(), payload=quote_payload(ref, fallback="Changed quote")
                )
            )
        assert len(session.sent) == 1
    finally:
        await adapter.stop()


async def test_wrong_profile_falls_back_before_native_reservation(config, context):
    session = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    try:
        bad = "another-profile:user:1:group:1:item:19"
        await adapter.deliver(rendered(attempt(), payload=quote_payload(bad)))
        assert session.sent[0]["quoted_item_id"] is None
        assert session.sent[0]["text"] == quote_payload(bad)["reply_fallback_text"]
        saved = json.loads(
            adapter.ledger.db.execute("SELECT request FROM handoffs").fetchone()[0]
        )
        assert len(saved) == 3 and saved[-1] == session.sent[0]["text"]
    finally:
        await adapter.stop()


async def test_uncertain_quote_is_not_resent_after_restart(config, context):
    first = NativeSession(config)
    first.fail_send = True
    adapter = SimplexChatAdapter(config, session_factory=lambda _: first)
    await adapter.start(context[0])
    ref = adapter.codec.reference("group:1", 19)
    result = rendered(attempt(), payload=quote_payload(ref))
    with pytest.raises(AdapterPermanentError):
        await adapter.deliver(result)
    await adapter.stop()
    second = NativeSession(config)
    adapter = SimplexChatAdapter(config, session_factory=lambda _: second)
    await adapter.start(context[0])
    try:
        with pytest.raises(AdapterPermanentError):
            await adapter.deliver(result)
        with pytest.raises(AdapterPermanentError):
            await adapter.deliver(rendered(attempt(2), payload=quote_payload(ref)))
        assert not second.sent
    finally:
        await adapter.stop()


async def test_unavailable_native_parent_uses_sealed_fallback_and_is_not_rechecked(
    config, context
):
    session = NativeSession(config)
    original_call = session.call
    checks = []

    async def call(op, **args):
        if op == "quote_target":
            checks.append(args)
            return False
        return await original_call(op, **args)

    session.call = call
    adapter = SimplexChatAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    try:
        ref = adapter.codec.reference("group:1", 19)
        result = rendered(attempt(), payload=quote_payload(ref))
        first = await adapter.deliver(result)
        await adapter.stop()
        restored = NativeSession(config)
        adapter = SimplexChatAdapter(config, session_factory=lambda _: restored)
        await adapter.start(context[0])
        second = await adapter.deliver(result)
        assert first.native_message_id == second.native_message_id
        assert not restored.sent
        assert all(op != "quote_target" for op, _ in restored.calls)
        assert len(checks) == 1
        assert len(session.sent) == 1
        assert session.sent[0]["text"] == result.payload["reply_fallback_text"]
        assert session.sent[0]["quoted_item_id"] is None
        request = json.loads(
            adapter.ledger.db.execute("SELECT request FROM handoffs").fetchone()[0]
        )
        assert request[3:] == [ref, result.payload["reply_fallback_text"]]
    finally:
        await adapter.stop()
