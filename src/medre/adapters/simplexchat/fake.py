"""SDK-free source and destination using the provider's codec and validation."""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import msgspec

from medre.core.contracts.adapter import (
    AdapterHandoffResult,
    AdapterInfo,
    AdapterSendError,
)

from .adapter import SimplexChatAdapter, validate_payload
from .codec import SimplexChatCodec
from .config import SimplexChatConfig, conversation


class FakeSimplexChatAdapter(SimplexChatAdapter):
    def __init__(
        self, adapter_id: str, config: SimplexChatConfig | None = None
    ) -> None:
        super().__init__(config or SimplexChatConfig(adapter_id, chats=("group:1",)))
        self.delivered_payloads = []

    async def start(self, ctx) -> None:
        self.ctx = ctx
        self._mark_started(ctx)
        self.state = "healthy"

    async def stop(self, timeout: float = 5.0) -> None:
        self.state = "unknown"

    async def deliver(self, result):
        validate_payload(result, self.config)
        if self.state != "healthy":
            raise AdapterSendError("SimpleX fake is not started")
        self.delivered_payloads.append(result)
        del self.delivered_payloads[:-1000]
        return AdapterHandoffResult(
            native_message_id=f"fake-simplexchat:{uuid4().hex}",
            native_channel_id=result.target_channel,
            note="SDK-free fake; no native receipt",
        )

    async def health_check(self):
        return AdapterInfo(
            self.adapter_id,
            self.platform,
            self.role,
            "0.1.0",
            self._capabilities,
            health=self.state,
        )

    def diagnostics(self):
        return {
            "fake": True,
            "health": self.state,
            "delivered": len(self.delivered_payloads),
        }

    def make_event(
        self,
        *,
        text: str,
        event_kind: str = "message.created",
        channel: str | None = None,
    ):
        if event_kind not in {"message.created", "message.text"}:
            raise ValueError("SimpleX fake supports text messages")
        channel = channel or self.config.chats[0]
        kind, number = conversation(channel)
        config = replace(self.config, chats=tuple(set(self.config.chats) | {channel}))
        info = (
            {"type": kind, "groupInfo": {"groupId": number}}
            if kind == "group"
            else {
                "type": kind,
                "contact": {"contactId": number, "localDisplayName": "Alice"},
            }
        )
        direction = (
            {
                "type": "groupRcv",
                "groupMember": {
                    "groupMemberId": 2,
                    "localDisplayName": "Alice",
                    "blockedByAdmin": False,
                },
            }
            if kind == "group"
            else {"type": "directRcv"}
        )
        item = {
            "chatDir": direction,
            "meta": {
                "itemId": uuid4().int % 2**62 + 1,
                "itemTs": datetime.now(timezone.utc).isoformat(),
                "itemEdited": False,
            },
            "content": {
                "type": "rcvMsgContent",
                "msgContent": {"type": "text", "text": text},
            },
        }
        event = SimplexChatCodec(config, "fake-profile").decode(info, item)
        if event is None:
            raise ValueError("Invalid SimpleX fake message")
        return msgspec.structs.replace(event, event_kind=event_kind)
