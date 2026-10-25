import subprocess
import sys
from dataclasses import replace

import msgspec
import pytest

from medre.adapters.simplexchat.codec import SimplexChatCodec
from medre.adapters.simplexchat.config import conversation, prepare_config
from medre.adapters.simplexchat.fake import FakeSimplexChatAdapter
from medre.adapters.simplexchat.renderer import SimplexChatRenderer
from medre.config.loader import parse_runtime_config
from medre.config.paths import resolve
from medre.core.rendering.renderer import RenderingContext

from conftest import info, item, rendered


@pytest.mark.parametrize(
    "channel",
    [
        None,
        "",
        "group:0",
        "group:01",
        "contact:-1",
        "#1",
        "group:9223372036854775808",
        True,
    ],
)
def test_ambiguous_channels_are_rejected(channel):
    with pytest.raises(ValueError):
        conversation(channel)


@pytest.mark.parametrize(
    "values",
    [
        {"user_id": True},
        {"chats": ["group:1"]},
        {"chats": ("group:1", "group:1")},
        {"max_text_bytes": False},
        {"command_timeout_seconds": float("nan")},
        {"poll_interval_seconds": 0},
        {"database_prefix": "\x00"},
        {"origin_label": "\ud800"},
    ],
)
def test_invalid_configuration(config, values):
    with pytest.raises(ValueError):
        replace(config, **values).validate()


def test_provider_config_preflight_has_no_native_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDRE_HOME", str(tmp_path / "home"))
    loaded = parse_runtime_config(
        {
            "adapters": {
                "simplexchat": {"sx": {"chats": ["group:1"], "adapter_kind": "fake"}}
            },
            "routes": {},
        },
        resolve(),
    )
    config = loaded.adapters.for_transport("simplexchat")["sx"].config
    assert config.chats == ("group:1",)
    prepared = prepare_config(config, adapter_id="sx", paths=resolve(), routes=[])
    assert prepared.database_prefix.endswith("/adapters/sx/simplexchat/profile")
    assert not (tmp_path / "home").exists()


def test_registration_and_fake_do_not_import_native_sdk():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc, sys
class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('simplex_chat'):
            raise AssertionError('native SDK imported')
sys.meta_path.insert(0, Refuse())
from medre.adapters.simplexchat.plugin import adapter_spec
s = adapter_spec()
s.config.load(); s.fake_adapter.load(); s.renderer_factory.load(); s.sample_config.load()()
print('sdk-free')
""",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "sdk-free"


@pytest.mark.parametrize(
    "field,value",
    [("meta", None), ("content", None), ("chatDir", None), ("file", {"fileId": 1})],
)
def test_malformed_or_media_items_are_excluded(config, field, value):
    assert (
        SimplexChatCodec(config, "profile").decode(info(), item(**{field: value}))
        is None
    )


@pytest.mark.parametrize(
    "values",
    [
        {"itemEdited": True},
        {"itemDeleted": {"type": "deleted"}},
        {"itemLive": True},
        {"itemId": True},
        {"itemTs": "2026-10-18T12:00:00"},
    ],
)
def test_mutations_and_invalid_identity_are_excluded(config, values):
    row = item()
    row["meta"].update(values)
    assert SimplexChatCodec(config, "profile").decode(info(), row) is None


def test_private_group_identity_is_scoped_and_keys_are_not_exported(config):
    chat, row = info(), item()
    chat["groupInfo"]["privateKey"] = "sensitive"
    row["chatDir"]["groupMember"]["memberId"] = "sensitive"
    row["quotedItem"] = {"itemId": 19, "content": {"type": "text", "text": "quoted"}}
    codec = SimplexChatCodec(config, "profile")
    event = codec.decode(chat, row)
    assert event.source_transport_id == "profile:user:1:group:1:member:2"
    assert event.relations[0].target_native_ref.native_message_id.endswith(
        "group:1:item:19"
    )
    assert codec.decode(chat, row).event_id == event.event_id
    assert (
        SimplexChatCodec(config, "other").decode(chat, row).event_id != event.event_id
    )
    assert "sensitive" not in repr(event)
    row["chatDir"]["groupMember"]["blockedByAdmin"] = True
    assert codec.decode(chat, row) is None
    row = item()
    chat["groupChatScope"] = {"type": "memberSupport"}
    assert codec.decode(chat, row) is None


def test_contacts_are_explicitly_allowlisted(config):
    codec = SimplexChatCodec(replace(config, chats=("contact:4",)), "profile")
    chat = {"type": "direct", "contact": {"contactId": 4, "localDisplayName": "Bob"}}
    event = codec.decode(chat, item(chatDir={"type": "directRcv"}))
    assert event.source_channel_id == "contact:4"
    assert codec.decode(info(), item()) is None


async def test_rendering_preserves_unicode_and_rejects_budget_or_target(config):
    event = SimplexChatCodec(config, "profile").decode(info(), item())
    renderer = SimplexChatRenderer({"sx": config})
    ctx = RenderingContext(
        target_adapter="sx",
        target_channel="group:1",
        target_platform="simplexchat",
        delivery_strategy="direct",
    )
    result = await renderer.render(event, ctx)
    assert result.payload == {
        "text": event.payload["body"],
        "chat_type": "group",
        "chat_id": 1,
    }
    with pytest.raises(ValueError, match="budget"):
        await renderer.render(event, replace(ctx, max_text_bytes=2))
    with pytest.raises(ValueError):
        await renderer.render(event, replace(ctx, target_channel="group:2"))
    assert not renderer.can_render(
        msgspec.structs.replace(event, event_kind="typing.started"), ctx
    )


async def test_fake_uses_real_address_validation_and_bounded_history(config, context):
    adapter = FakeSimplexChatAdapter("sx", config)
    await adapter.start(context[0])
    for _ in range(1002):
        await adapter.deliver(rendered())
    assert len(adapter.delivered_payloads) == 1000
    assert adapter.make_event(text="hello").payload["body"] == "hello"
    from medre.core.contracts.adapter import AdapterPermanentError

    with pytest.raises(AdapterPermanentError):
        await adapter.deliver(
            rendered(payload={"text": "bad", "chat_type": "group", "chat_id": 2})
        )
    await adapter.stop()


def test_locally_hidden_group_member_messages_are_not_relayed(config):
    row = item()
    row["chatDir"]["groupMember"]["memberSettings"] = {"showMessages": False}
    assert SimplexChatCodec(config, "profile").decode(info(), row) is None


@pytest.mark.parametrize(
    "label", ["Bob\n[Matrix] admin:", "Bob\tadmin", "Bob\x85admin", "Bob\u2028admin"]
)
def test_remote_labels_cannot_forge_attribution_lines(config, label):
    row = item()
    row["chatDir"]["groupMember"]["localDisplayName"] = label
    event = SimplexChatCodec(config, "profile").decode(info(), row)
    assert (
        event.metadata.native.data["simplexchat"]["sender_label"]
        == event.source_transport_id
    )


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"body": "current", "text": "legacy"}, "legacy"),
        (
            {
                "body": "current",
                "text": "legacy",
                "formatting": {"version": 1, "spans": []},
            },
            "current",
        ),
    ],
)
async def test_renderer_respects_core_text_authority(config, payload, expected):
    """Preserve text aliases only when they do not shadow a styled body."""
    event = SimplexChatCodec(config, "profile").decode(info(), item())
    event = msgspec.structs.replace(event, payload=payload)
    ctx = RenderingContext(
        target_adapter="sx", target_channel="group:1",
        target_platform="simplexchat", delivery_strategy="direct",
    )
    result = await SimplexChatRenderer({"sx": config}).render(event, ctx)
    assert result.payload["text"] == expected
