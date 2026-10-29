"""Core-bound SimpleX reply rendering must preserve scoped destination identity."""

import msgspec

from conftest import info, item
from medre.adapters.simplexchat.codec import SimplexChatCodec
from medre.adapters.simplexchat.renderer import SimplexChatRenderer
from medre.core.events.canonical import EventRelation, RelationTargetFact
from medre.core.rendering.renderer import RenderingContext


def test_codec_rejects_cross_profile_chat_user_or_noncanonical_ids(config):
    codec = SimplexChatCodec(config, "profile-uuid")
    good = codec.reference("group:1", 19)
    assert codec.quote_item_id("group:1", good) == 19
    for bad in (
        "other:user:1:group:1:item:19",
        "profile-uuid:user:2:group:1:item:19",
        "profile-uuid:user:1:group:2:item:19",
        "profile-uuid:user:1:group:1:item:019",
        "profile-uuid:user:1:group:1:item:0",
        "profile-uuid:user:1:group:1:item:9223372036854775808",
        "profile-uuid:user:1:group:1:item:19\n",
    ):
        assert codec.quote_item_id("group:1", bad) is None


async def test_renderer_only_uses_core_bound_destination_fact(config):
    codec = SimplexChatCodec(config, "profile-uuid")
    event = codec.decode(info(), item())
    assert event is not None
    relation = EventRelation(
        "reply",
        "original",
        None,
        None,
        "Hello earlier",
        target_fact=RelationTargetFact(
            "bound",
            adapter="sx",
            native_channel_id="group:1",
            native_message_id=codec.reference("group:1", 19),
        ),
    )
    event = msgspec.structs.replace(event, relations=(relation,))
    renderer = SimplexChatRenderer({"sx": config})
    ctx = RenderingContext("direct", "sx", "group:1", "simplexchat")
    result = await renderer.render(event, ctx)
    assert result.payload["reply_native_ref"] == codec.reference("group:1", 19)
    assert "Hello earlier" in result.payload["reply_fallback_text"]
    changed = msgspec.structs.replace(
        relation,
        target_fact=RelationTargetFact(
            "bound",
            adapter="sx",
            native_channel_id="group:2",
            native_message_id=codec.reference("group:2", 19),
        ),
    )
    fallback = await renderer.render(
        msgspec.structs.replace(event, relations=(changed,)), ctx
    )
    assert "reply_native_ref" not in fallback.payload
    assert fallback.fallback_applied == "relation_reply"
    assert "Hello earlier" in fallback.payload["text"]
