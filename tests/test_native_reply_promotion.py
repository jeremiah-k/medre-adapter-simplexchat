"""Advertised replies choose native rendering while preserving target fallback."""

from dataclasses import replace

import msgspec
from conftest import info, item
from medre.core.events.canonical import EventRelation, RelationTargetFact
from medre.core.planning.capability_decision import resolver
from medre.core.rendering.renderer import RenderingContext

from medre.adapters.simplexchat.adapter import SimplexChatAdapter
from medre.adapters.simplexchat.codec import SimplexChatCodec
from medre.adapters.simplexchat.fake import FakeSimplexChatAdapter
from medre.adapters.simplexchat.renderer import SimplexChatRenderer


async def test_advertised_native_reply_drives_core_and_keeps_scope_fallback(config):
    real = await SimplexChatAdapter(config).health_check()
    fake = await FakeSimplexChatAdapter(config.adapter_id, config).health_check()
    assert real.capabilities == fake.capabilities
    assert real.capabilities.replies == "native"
    assert real.capabilities.threads == "fallback"
    codec = SimplexChatCodec(config, "qualified-profile")
    parent_ref = codec.reference("group:1", 19)
    event = codec.decode(info(), item())
    assert event is not None
    relation = EventRelation(
        "reply",
        "parent-event",
        None,
        None,
        "retained parent",
        target_fact=RelationTargetFact(
            "bound",
            adapter=config.adapter_id,
            native_channel_id="group:1",
            native_message_id=parent_ref,
        ),
    )
    event = msgspec.structs.replace(event, relations=(relation,))
    decision = resolver.decide(
        event, real.capabilities, target_adapter=config.adapter_id
    )
    assert decision.delivery_strategy == "direct"
    context = RenderingContext(
        decision.delivery_strategy, config.adapter_id, "group:1", "simplexchat"
    )
    renderer = SimplexChatRenderer({config.adapter_id: config})
    rendered = await renderer.render(event, context)
    assert rendered.payload["reply_native_ref"] == parent_ref
    assert rendered.payload["text"].count("Hello <SimpleX> & 世界") == 1
    wrong_scope = msgspec.structs.replace(
        relation,
        target_fact=RelationTargetFact(
            "bound",
            adapter=config.adapter_id,
            native_channel_id="group:2",
            native_message_id=codec.reference("group:2", 19),
        ),
    )
    fallback = await renderer.render(
        msgspec.structs.replace(event, relations=(wrong_scope,)), context
    )
    assert "reply_native_ref" not in fallback.payload
    assert fallback.fallback_applied == "relation_reply"
    assert "retained parent" in fallback.payload["text"]

    # Native replies must not upgrade thread semantics. The most restrictive
    # relation still controls the core strategy for a mixed-relation event.
    thread = msgspec.structs.replace(
        relation, relation_type="thread", fallback_text="retained root"
    )
    for relations in ((thread,), (relation, thread)):
        threaded = msgspec.structs.replace(event, relations=relations)
        thread_decision = resolver.decide(
            threaded, real.capabilities, target_adapter=config.adapter_id
        )
        assert thread_decision.delivery_strategy == "fallback_text"
        result = await renderer.render(
            threaded,
            replace(context, delivery_strategy=thread_decision.delivery_strategy),
        )
        assert "reply_native_ref" not in result.payload
        assert "reply_fallback_text" not in result.payload
        assert "retained root" in result.payload["text"]
        assert result.payload["text"].count("Hello <SimpleX> & 世界") == 1
