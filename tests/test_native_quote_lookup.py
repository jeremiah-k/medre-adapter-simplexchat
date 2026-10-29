"""Native parent inspection selects a quote only for the exact retained item."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from medre.adapters.simplexchat.worker import quote_target_available


def parent(chat_type="group"):
    info = (
        {"type": "group", "groupInfo": {"groupId": 1}}
        if chat_type == "group"
        else {"type": "direct", "contact": {"contactId": 1}}
    )
    return {
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


@pytest.mark.parametrize("chat_type, address", [("group", "#1"), ("direct", "@1")])
async def test_native_parent_lookup_preserves_chat_kind(chat_type, address):
    api = SimpleNamespace(send_chat_cmd=AsyncMock(return_value=parent(chat_type)))
    assert await quote_target_available(
        api, user_id=1, chat_type=chat_type, chat_id=1, item_id=19
    )
    api.send_chat_cmd.assert_awaited_once_with(f"/_get item info {address} 19")


@pytest.mark.parametrize(
    "path, value",
    [
        (("type",), "chatCmdError"),
        (("user", "userId"), 2),
        (("user", "userId"), True),
        (("chatItem", "chatInfo", "groupInfo", "groupId"), 2),
        (("chatItem", "chatItem", "meta", "itemId"), 20),
        (("chatItem", "chatItem", "meta", "itemId"), True),
        (("chatItem", "chatItem", "meta", "itemDeleted"), {"deletedTs": "expired"}),
        (("chatItem", "chatItem", "content", "type"), "rcvDeleted"),
        (("chatItem",), None),
    ],
)
async def test_wrong_or_unavailable_native_parent_uses_fallback(path, value):
    response = deepcopy(parent())
    target = response
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    api = SimpleNamespace(send_chat_cmd=AsyncMock(return_value=response))
    assert not await quote_target_available(
        api, user_id=1, chat_type="group", chat_id=1, item_id=19
    )


async def test_native_lookup_failure_can_degrade_but_cancellation_propagates():
    api = SimpleNamespace(
        send_chat_cmd=AsyncMock(side_effect=RuntimeError("read unavailable"))
    )
    assert not await quote_target_available(
        api, user_id=1, chat_type="group", chat_id=1, item_id=19
    )
    api.send_chat_cmd.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await quote_target_available(
            api, user_id=1, chat_type="group", chat_id=1, item_id=19
        )
