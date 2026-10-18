"""Normalize the official API's group/direct chat items without global identities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from medre.core.events.canonical import CanonicalEvent, EventRelation, NativeRef
from medre.core.events.metadata import EventMetadata, NativeMetadata, TransportMetadata

from .config import SimplexChatConfig, plain_text


def _mapping(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def chat_channel(info: Mapping) -> str | None:
    if not isinstance(info, Mapping):
        return None
    kind = info.get("type")
    if kind == "group":
        number = _mapping(info.get("groupInfo")).get("groupId")
        prefix = "group"
    elif kind == "direct":
        number = _mapping(info.get("contact")).get("contactId")
        prefix = "contact"
    else:
        return None
    if type(number) is not int or not 1 <= number <= 2**63 - 1:
        return None
    return f"{prefix}:{number}"


class SimplexChatCodec:
    def __init__(self, config: SimplexChatConfig, profile: str) -> None:
        self.config, self.profile = config, profile

    def reference(self, channel: str, item_id: int) -> str:
        return f"{self.profile}:user:{self.config.user_id}:{channel}:item:{item_id}"

    def decode(self, info: Mapping, item: Mapping) -> CanonicalEvent | None:
        if not isinstance(info, Mapping) or not isinstance(item, Mapping):
            return None
        channel = chat_channel(info)
        if channel not in self.config.chats:
            return None
        content, meta, direction = (
            _mapping(item.get("content")),
            _mapping(item.get("meta")),
            _mapping(item.get("chatDir")),
        )
        message = _mapping(content.get("msgContent"))
        item_id = meta.get("itemId")
        if (
            content.get("type") != "rcvMsgContent"
            or message.get("type") != "text"
            or type(item_id) is not int
            or not 1 <= item_id <= 2**63 - 1
            or meta.get("itemEdited") is not False
            or meta.get("itemDeleted") is not None
            or meta.get("itemLive") is True
            or item.get("file") is not None
        ):
            return None
        text = message.get("text")
        if (
            not plain_text(text)
            or not text.strip()
            or len(text.encode()) > self.config.max_text_bytes
        ):
            return None
        if info["type"] == "group":
            scope = _mapping(info.get("groupChatScope"))
            if scope and scope.get("type") != "main":
                return None
            member = _mapping(direction.get("groupMember"))
            sender_id = member.get("groupMemberId")
            if (
                direction.get("type") != "groupRcv"
                or type(sender_id) is not int
                or sender_id <= 0
                or member.get("blockedByAdmin") is not False
                or _mapping(member.get("memberSettings")).get("showMessages") is False
            ):
                return None
            sender = f"{self.profile}:user:{self.config.user_id}:{channel}:member:{sender_id}"
            label = member.get("localDisplayName")
        else:
            if direction.get("type") != "directRcv":
                return None
            contact = _mapping(info["contact"])
            sender = f"{self.profile}:user:{self.config.user_id}:{channel}"
            label = contact.get("localDisplayName")
        try:
            timestamp = datetime.fromisoformat(meta["itemTs"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                return None
            timestamp = timestamp.astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return None
        reference = self.reference(channel, item_id)
        quote = item.get("quotedItem")
        relations = ()
        if (
            isinstance(quote, Mapping)
            and type(quote.get("itemId")) is int
            and quote["itemId"] > 0
        ):
            quoted_text = _mapping(quote.get("content")).get("text")
            relations = (
                EventRelation(
                    "reply",
                    None,
                    NativeRef(
                        self.config.adapter_id,
                        channel,
                        self.reference(channel, quote["itemId"]),
                    ),
                    None,
                    quoted_text if plain_text(quoted_text) else None,
                ),
            )
        label = (
            label
            if plain_text(label) and label.strip() and label.isprintable()
            else sender
        )
        return CanonicalEvent(
            event_id=str(
                uuid5(NAMESPACE_URL, f"medre:{self.config.adapter_id}:{reference}")
            ),
            event_kind="message.created",
            schema_version=1,
            timestamp=timestamp,
            source_adapter=self.config.adapter_id,
            source_transport_id=sender,
            source_channel_id=channel,
            parent_event_id=None,
            lineage=(),
            relations=relations,
            payload={"body": text},
            source_native_ref=NativeRef(self.config.adapter_id, channel, reference),
            metadata=EventMetadata(
                transport=TransportMetadata(protocol="simplexchat", substrate="ip"),
                native=NativeMetadata(
                    data={
                        "simplexchat": {
                            "schema_version": 1,
                            "profile_id": self.profile,
                            "user_id": self.config.user_id,
                            "conversation": channel,
                            "item_id": item_id,
                            "sender_label": label,
                        }
                    }
                ),
            ),
        )
