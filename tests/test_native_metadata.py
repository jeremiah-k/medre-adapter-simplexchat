"""Native metadata the SimpleX SDK supplies and the codec must carry.

The official client supplies group-member and contact profiles
(``displayName``/``fullName``), the shared stable ``memberId``, per-item
verification state and link/mention flags. The codec captures the versioned
native namespace; the attribution projector prefers the peer-declared profile
name over the local word-pair alias. Optional fields degrade to absence: the
codec never invents profile data that the SDK did not supply.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from medre.adapters.simplexchat.codec import SimplexChatCodec
from medre.adapters.simplexchat.metadata import project_attribution
from medre.adapters.simplexchat.renderer import SimplexChatRenderer
from medre.core.rendering.renderer import RenderingContext

from conftest import NOW, info, item

DEFAULT_RELAY_PREFIX = "[{origin_label}] {sender}: "


def profile(display_name="Jeremiah", full_name="Jeremiah K.", **updates):
    return {"displayName": display_name, "fullName": full_name, **updates}


def profiled_item(**updates):
    row = item()
    row["chatDir"]["groupMember"]["memberProfile"] = profile(
        **updates.pop("profile", {})
    )
    row["meta"].update(updates)
    return row


def render_context():
    return RenderingContext(
        target_adapter="sx",
        target_channel="group:1",
        target_platform="simplexchat",
        delivery_strategy="direct",
    )


def prefixed_renderer(config):
    return SimplexChatRenderer(
        {"sx": replace(config, relay_prefix=DEFAULT_RELAY_PREFIX)},
        {"sx": SimpleNamespace(platform="simplexchat", origin_label="Mesh")},
    )


def native(config, row):
    event = SimplexChatCodec(config, "profile").decode(info(), row)
    assert event is not None
    return event, event.metadata.native.data["simplexchat"]


async def test_profile_display_name_reaches_the_relay_prefix(config):
    """The peer-declared profile name outranks the local word-pair alias."""
    event, native_ns = native(config, profiled_item())
    assert native_ns["sender_label"] == "Alice"
    assert native_ns["sender_display_name"] == "Jeremiah"
    assert native_ns["sender_full_name"] == "Jeremiah K."
    result = await prefixed_renderer(config).render(event, render_context())
    assert result.payload["text"] == "[Mesh] Jeremiah: Hello <SimpleX> & 世界"


async def test_attribution_falls_back_through_full_name_and_local_alias(config):
    """Only SDK-supplied names are used; missing fields degrade, never invent."""
    renderer = prefixed_renderer(config)
    event, native_ns = native(
        config, profiled_item(profile={"displayName": "", "fullName": "Jeremiah K."})
    )
    assert "sender_display_name" not in native_ns
    assert native_ns["sender_full_name"] == "Jeremiah K."
    result = await renderer.render(event, render_context())
    assert result.payload["text"].startswith("[Mesh] Jeremiah K.: ")

    event, native_ns = native(
        config, profiled_item(profile={"displayName": "", "fullName": ""})
    )
    assert "sender_display_name" not in native_ns
    assert "sender_full_name" not in native_ns
    result = await renderer.render(event, render_context())
    assert result.payload["text"].startswith("[Mesh] Alice: ")

    event, _ = native(
        config, profiled_item(profile={"displayName": "\nadmin", "fullName": ""})
    )
    result = await renderer.render(event, render_context())
    assert result.payload["text"].startswith("[Mesh] Alice: ")


async def test_direct_conversations_project_the_contact_profile(config):
    codec = SimplexChatCodec(replace(config, chats=("contact:4",)), "profile")
    chat = {
        "type": "direct",
        "contact": {
            "contactId": 4,
            "localDisplayName": "word-pair",
            "profile": profile(),
        },
    }
    event = codec.decode(chat, item(chatDir={"type": "directRcv"}))
    native_ns = event.metadata.native.data["simplexchat"]
    assert native_ns["sender_label"] == "word-pair"
    assert native_ns["sender_display_name"] == "Jeremiah"
    result = await prefixed_renderer(config).render(event, render_context())
    assert result.payload["text"] == "[Mesh] Jeremiah: Hello <SimpleX> & 世界"


def test_member_id_is_captured_as_the_stable_shared_identity(config):
    """The shared member ID survives localDisplayName changes; direct chats have none."""
    row = profiled_item()
    row["chatDir"]["groupMember"]["memberId"] = "opaque-shared-id"
    row["chatDir"]["groupMember"]["memberRole"] = "member"
    event, native_ns = native(config, row)
    assert native_ns["member_id"] == "opaque-shared-id"
    assert native_ns["member_role"] == "member"
    _, bare = native(config, profiled_item())
    assert "member_id" not in bare
    assert "member_role" not in bare
    codec = SimplexChatCodec(replace(config, chats=("contact:4",)), "profile")
    chat = {
        "type": "direct",
        "contact": {"contactId": 4, "localDisplayName": "Bob", "profile": profile()},
    }
    direct = codec.decode(chat, item(chatDir={"type": "directRcv"}))
    assert "member_id" not in direct.metadata.native.data["simplexchat"]


@pytest.mark.parametrize(
    "verified,expected",
    [
        ({"type": "signed", "sigStatus": "verified"}, "verified"),
        ({"type": "signed", "sigStatus": "signedNoKey"}, "signed_no_key"),
        ({"type": "sigMissing"}, "sig_missing"),
        ({"type": "unexpected"}, None),
        (None, None),
        ("signed", None),
    ],
)
def test_msg_verification_is_captured_and_maps_to_transport_signature(
    config, verified, expected
):
    row = profiled_item(msgVerified=verified)
    event, native_ns = native(config, row)
    signature = event.metadata.transport.signature_valid
    if expected is None:
        assert "msg_verified" not in native_ns
        assert signature is None
    else:
        assert native_ns["msg_verified"] == expected
        assert signature is (
            True
            if expected == "verified"
            else False if expected == "sig_missing" else None
        )


def test_mention_and_link_flags_are_captured_only_when_boolean(config):
    event, native_ns = native(config, profiled_item(userMention=True, hasLink=False))
    assert native_ns["user_mention"] is True
    assert native_ns["has_link"] is False
    event, native_ns = native(config, profiled_item(userMention="yes", hasLink=None))
    assert "user_mention" not in native_ns
    assert "has_link" not in native_ns


def test_public_facts_expose_only_the_bounded_allowlist(config):
    row = profiled_item(msgVerified={"type": "signed", "sigStatus": "verified"})
    row["chatDir"]["groupMember"]["memberRole"] = "admin"
    row["chatDir"]["groupMember"]["memberId"] = "opaque-shared-id"
    _, native_ns = native(config, row)
    assert native_ns["public_facts"] == {
        "msg_verified": "verified",
        "member_role": "admin",
    }
    _, bare = native(config, profiled_item())
    assert "public_facts" not in bare


def test_bridge_context_carries_the_simplex_namespace_facts(config):
    from medre.core.rendering.bridge_context import build_bridge_context

    row = profiled_item(msgVerified={"type": "signed", "sigStatus": "verified"})
    row["chatDir"]["groupMember"]["memberRole"] = "admin"
    event, _ = native(config, row)
    context = build_bridge_context(event)
    facts = context["source"]["native_facts"]
    assert facts["namespace"] == "simplexchat"
    assert facts["fields"] == {"msg_verified": "verified", "member_role": "admin"}


def test_projector_prefers_profile_names_and_keeps_the_scoped_handle():
    projected = project_attribution(
        {
            "simplexchat": {
                "schema_version": 1,
                "sender_label": "Alice",
                "sender_display_name": "Jeremiah",
                "sender_full_name": "Jeremiah K.",
            }
        },
        source_transport_id="sx:user:1:group:1:member:2",
    )
    assert projected["source_sender_label"] == "Jeremiah"
    assert projected["source_sender_handle"] == "sx:user:1:group:1:member:2"
    legacy = project_attribution(
        {"simplexchat": {"schema_version": 1, "sender_label": "Alice"}},
        source_transport_id="sx:user:1:group:1:member:2",
    )
    assert legacy["source_sender_label"] == "Alice"


def test_bare_namespace_projection_ignores_blank_profile_names():
    projected = project_attribution(
        {
            "simplexchat": {
                "schema_version": 1,
                "sender_display_name": "",
                "sender_full_name": "   ",
            }
        }
    )
    assert projected["source_sender_label"] is None


def test_native_namespace_timestamp_and_label_semantics_are_unchanged(config):
    row = profiled_item()
    event, native_ns = native(config, row)
    assert native_ns["schema_version"] == 1
    assert native_ns["item_id"] == row["meta"]["itemId"]
    assert event.timestamp == NOW
