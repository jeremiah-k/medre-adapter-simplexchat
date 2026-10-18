import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from medre.adapters.simplexchat.config import SimplexChatConfig
from medre.core.contracts.adapter import AdapterContext
from medre.core.events.delivery import DeliveryAttemptProvenance
from medre.core.rendering.renderer import RenderingResult

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def info():
    return {
        "type": "group",
        "groupInfo": {"groupId": 1, "membership": {"memberStatus": "connected"}},
    }


def item(number=20, **updates):
    return {
        "meta": {"itemId": number, "itemTs": NOW.isoformat(), "itemEdited": False},
        "chatDir": {
            "type": "groupRcv",
            "groupMember": {
                "groupMemberId": 2,
                "localDisplayName": "Alice",
                "blockedByAdmin": False,
            },
        },
        "content": {
            "type": "rcvMsgContent",
            "msgContent": {"type": "text", "text": "Hello <SimpleX> & 世界"},
        },
        **updates,
    }


@pytest.fixture
def config(tmp_path):
    prefix = tmp_path / "profile"
    prefix.with_name("profile_chat.db").touch()
    return SimplexChatConfig(
        "sx",
        database_prefix=str(prefix),
        state_dir=str(tmp_path / "state"),
        chats=("group:1",),
        relay_prefix="",
        poll_interval_seconds=0.01,
    )


@pytest.fixture
def context():
    checkpoints = {}

    async def commit(stream, cursor, metadata):
        checkpoints[stream] = SimpleNamespace(cursor=cursor, metadata_json=metadata)

    ctx = AdapterContext(
        "sx",
        AsyncMock(),
        logging.getLogger("simplex-test"),
        lambda: NOW,
        asyncio.Event(),
        admit_inbound=AsyncMock(),
        load_checkpoint=AsyncMock(side_effect=lambda key: checkpoints.get(key)),
        commit_checkpoint=AsyncMock(side_effect=commit),
    )
    return ctx, checkpoints


def attempt(number=1):
    return DeliveryAttemptProvenance(
        "event", "plan", "sx", "group:1", "outbox", number, "live"
    )


def rendered(provenance=None, **updates):
    return RenderingResult(
        **{
            "event_id": "event",
            "target_adapter": "sx",
            "target_channel": "group:1",
            "payload": {"text": "Rendered message", "chat_type": "group", "chat_id": 1},
            "attempt_provenance": provenance,
            **updates,
        }
    )


async def wait_until(predicate):
    async with asyncio.timeout(3):
        loop = asyncio.get_running_loop()
        while not predicate():
            future = loop.create_future()
            timer = loop.call_later(0.001, future.set_result, None)
            try:
                await future
            finally:
                timer.cancel()


class NativeSession:
    def __init__(self, config, rows=None):
        self.config = config
        self.rows = rows if rows is not None else []
        self.lease = SimpleNamespace(namespace="profile-uuid")
        self.wake = asyncio.Event()
        self.events = []
        self.calls = []
        self.started = False
        self.closed = False
        self.sent = []
        self.fail_send = False

    async def start(self, **kwargs):
        pass

    async def stop(self):
        self.closed = True

    async def call(self, op, **args):
        self.calls.append((op, args))
        if op == "start":
            self.started = True
            return True
        if op == "history":
            assert (
                not self.started
            ), "History recovery must finish before reception starts"
            rows = self.rows
            before = args.get("before")
            if before:
                rows = rows[
                    : next(
                        i
                        for i, row in enumerate(rows)
                        if row["meta"]["itemId"] == before
                    )
                ]
            return {"chatInfo": info(), "chatItems": deepcopy(rows[-args["count"] :])}
        if op == "events":
            result, self.events = self.events, []
            return result
        if op == "send":
            self.sent.append(args)
            if self.fail_send:
                raise RuntimeError("native outcome unknown")
            return [
                {
                    "chatInfo": info(),
                    "chatItem": item(
                        100,
                        chatDir={"type": "groupSnd"},
                        content={
                            "type": "sndMsgContent",
                            "msgContent": {"type": "text", "text": args["text"]},
                        },
                    ),
                }
            ]
        raise AssertionError(op)

    def incoming(self, row):
        self.rows.append(row)
        self.events.append({"chatInfo": info(), "chatItem": row})
        self.wake.set()
