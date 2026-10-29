"""Qualify the pinned SimpleX quote boundary before enabling native replies."""

from importlib.metadata import version
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.simplexchat_sdk


async def test_pinned_chat_api_transmits_scoped_quoted_item_id():
    """The 7.0.2 API models a reply as one quoted item in one chat ref."""
    from simplex_chat.api import ChatApi

    assert version("simplex-chat") == "7.0.2"
    api = ChatApi(0)  # Do not open the native controller for API-shape tests.
    send = AsyncMock(return_value=[{"chatInfo": {}, "chatItem": {}}])
    with patch.object(ChatApi, "api_send_messages", new=send):
        await api.api_send_text_message(
            {"chatType": "group", "chatId": 10}, "Hello ☃", in_reply_to=19
        )
    assert send.await_args.args == (
        {"chatType": "group", "chatId": 10},
        [
            {
                "msgContent": {"type": "text", "text": "Hello ☃"},
                "mentions": {},
                "quotedItemId": 19,
            }
        ],
    )


async def test_pinned_chat_api_ordinary_text_does_not_invent_a_quote():
    from simplex_chat.api import ChatApi

    api = ChatApi(0)
    send = AsyncMock(return_value=[])
    with patch.object(ChatApi, "api_send_messages", new=send):
        await api.api_send_text_message({"chatType": "direct", "chatId": 22}, "Plain")
    assert "quotedItemId" not in send.await_args.args[1][0]


@pytest.mark.parametrize("chat_type, address", [("direct", "@10"), ("group", "#10")])
async def test_pinned_send_messages_serializes_quote_into_native_command(
    chat_type, address
):
    """The provider's low-level send API retains quote scope at the command boundary."""
    import json

    from simplex_chat.api import ChatApi

    api = ChatApi(0)
    items = [{"chatInfo": {}, "chatItem": {}}]
    command = AsyncMock(return_value={"type": "newChatItems", "chatItems": items})
    composed = [
        {
            "msgContent": {"type": "text", "text": "Hello ☃"},
            "mentions": {},
            "quotedItemId": 19,
        }
    ]
    with patch.object(ChatApi, "send_chat_cmd", new=command):
        assert (
            await api.api_send_messages({"chatType": chat_type, "chatId": 10}, composed)
            == items
        )
    command.assert_awaited_once()
    header, payload = command.await_args.args[0].split(" json ", 1)
    assert header == f"/_send {address}"
    assert json.loads(payload) == composed


@pytest.mark.parametrize("chat_type, address", [("group", "#1"), ("direct", "@1")])
async def test_pinned_sdk_native_item_inspection_preserves_scope(chat_type, address):
    from simplex_chat.api import ChatApi
    from medre.adapters.simplexchat.worker import quote_target_available

    info = (
        {"type": "group", "groupInfo": {"groupId": 1}}
        if chat_type == "group"
        else {"type": "direct", "contact": {"contactId": 1}}
    )
    response = {
        "type": "chatItemInfo",
        "user": {"userId": 1},
        "chatItem": {
            "chatInfo": info,
            "chatItem": {
                "meta": {"itemId": 19},
                "content": {"type": "rcvMsgContent"},
            },
        },
    }
    native = AsyncMock(return_value=response)
    with patch("simplex_chat.core.chat_send_cmd", new=native):
        assert await quote_target_available(
            ChatApi(0), user_id=1, chat_type=chat_type, chat_id=1, item_id=19
        )
    native.assert_awaited_once_with(0, f"/_get item info {address} 19")
