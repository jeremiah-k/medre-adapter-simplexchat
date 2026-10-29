"""Own the official native client inside an independently terminable process."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    # Keep generated SDK type imports outside native worker startup; fake
    # boundary stubs and offline diagnostics need only the runtime ChatApi.
    from simplex_chat.types import T


async def quote_target_available(
    api: Any, *, user_id: int, chat_type: str, chat_id: int, item_id: int
) -> bool:
    """Check a retained parent through the native owner without sending bytes.

    Missing, deleted or mismatched native items select the sealed text fallback.
    A failed read is also safe to degrade; cancellation still stops the owner.
    """
    from .codec import chat_channel

    if chat_type not in {"group", "direct"} or any(
        type(value) is not int or not 1 <= value <= 2**63 - 1
        for value in (user_id, chat_id, item_id)
    ):
        raise ValueError("Invalid native quote scope")
    address = ("#" if chat_type == "group" else "@") + str(chat_id)
    try:
        response = await api.send_chat_cmd(f"/_get item info {address} {item_id}")
    except Exception:
        return False
    if not isinstance(response, dict) or response.get("type") != "chatItemInfo":
        return False
    user, record = response.get("user"), response.get("chatItem")
    if not isinstance(user, dict) or not isinstance(record, dict):
        return False
    native_user = user.get("userId")
    info, item = record.get("chatInfo"), record.get("chatItem")
    if not isinstance(info, dict) or not isinstance(item, dict):
        return False
    meta, content = item.get("meta"), item.get("content")
    if not isinstance(meta, dict) or not isinstance(content, dict):
        return False
    native_id = meta.get("itemId")
    return (
        type(native_user) is int
        and native_user == user_id
        and chat_channel(info)
        == f"{'group' if chat_type == 'group' else 'contact'}:{chat_id}"
        and type(native_id) is int
        and native_id == item_id
        and meta.get("itemDeleted") is None
        and content.get("type") in {"sndMsgContent", "rcvMsgContent"}
    )


class NativeEvents:
    """Bound both native event count and serialized IPC memory."""

    def __init__(self, user_id: int, chats: list[str]) -> None:
        self.user_id, self.chats = user_id, frozenset(chats)
        self.records: list[tuple[dict[str, Any], int]] = []
        self.size = 0

    def feed(self, event: dict[str, Any]) -> bool:
        from .codec import chat_channel

        if event.get("type") != "newChatItems":
            return False
        user_id = event["user"]["userId"]
        if type(user_id) is not int:
            raise ValueError("Malformed native user identity")
        if user_id != self.user_id:
            return False
        added = False
        for record in event["chatItems"]:
            if chat_channel(record["chatInfo"]) not in self.chats:
                continue
            item = record["chatItem"]
            if (
                item["content"].get("type") != "rcvMsgContent"
                or item.get("file") is not None
            ):
                continue
            if item["content"]["msgContent"].get("type") != "text":
                continue
            size = len(json.dumps(record).encode("utf-8"))
            if len(self.records) >= 256 or self.size + size > 1024 * 1024:
                raise RuntimeError("Native ingress buffer overflow")
            self.records.append((record, size))
            self.size += size
            added = True
        return added

    def take(self) -> list[dict[str, Any]]:
        rows = self.records[:32]
        del self.records[:32]
        self.size -= sum(size for _, size in rows)
        return [record for record, _ in rows]


def emit(**data: Any) -> None:
    print("SXRPC " + json.dumps(data, separators=(",", ":")), flush=True)


async def main() -> None:
    os.umask(0o077)
    settings = json.loads(await asyncio.to_thread(sys.stdin.readline))
    from simplex_chat import ChatApi, MigrationConfirmation, SqliteDb

    api = await ChatApi.init(
        SqliteDb(settings["database_prefix"], settings["database_key"]),
        confirm=MigrationConfirmation.YES_UP,
    )
    queue = NativeEvents(settings["user_id"], settings["chats"])
    connected = False
    secondary = False
    try:
        user = await api.api_get_active_user()
        if user is None and settings.get("create_name"):
            user = await api.api_create_active_user(
                {
                    "displayName": settings["create_name"],
                    "fullName": "",
                    "peerType": "bot",
                }
            )
        if user is not None:
            response = await api.send_chat_cmd("/_start main=off snd_files=off")
            if response.get("type") not in {"chatStarted", "chatRunning"}:
                raise RuntimeError("Secondary controller rejected")
            secondary = True
        emit(kind="ready", user_id=user["userId"] if user else None)

        async def drain() -> None:
            nonlocal connected

            try:
                while True:
                    event = await api.recv_chat_event(wait_us=100_000)
                    if not event:
                        continue
                    if (
                        event.get("type") in {"contactConnected", "userJoinedGroup"}
                        and event["user"]["userId"] == settings["user_id"]
                    ):
                        connected = True
                        emit(kind="wake")
                    if queue.feed(event):
                        emit(kind="wake")
            except Exception:
                # A blocked stdin reader may outlive TaskGroup cancellation.
                # Notify the owner immediately so it closes stdin and reaps us.
                emit(kind="fatal", error="native_event_stream_failed")
                raise

        async with asyncio.TaskGroup() as tasks:
            events = tasks.create_task(drain())
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                operation, args = command["op"], command.get("args", {})
                try:
                    if operation == "close":
                        events.cancel()
                        break
                    if operation == "start":
                        if secondary:
                            await api.send_chat_cmd("/_stop")
                            secondary = False
                        await api.start_chat()
                        result: object = True
                    elif operation == "history":
                        kind, number = args["chat_type"], args["chat_id"]
                        before = args.get("before")
                        if before:
                            ref = ("#" if kind == "group" else "@") + str(int(number))
                            response = await api.send_chat_cmd(
                                f"/_get chat {ref} before={int(before)} count=64"
                            )
                            if response.get("type") != "apiChat":
                                raise RuntimeError("Unexpected native history response")
                            result = response["chat"]
                        else:
                            result = await api.api_get_chat(
                                kind, number, args.get("count", 64)
                            )

                    elif operation == "events":
                        result = queue.take()
                        if queue.records:
                            emit(kind="wake")
                    elif operation == "connection_status":
                        result = connected
                    elif operation == "quote_target":
                        result = await quote_target_available(
                            api,
                            user_id=settings["user_id"],
                            chat_type=args["chat_type"],
                            chat_id=args["chat_id"],
                            item_id=args["item_id"],
                        )
                    elif operation == "send":
                        quote = args.get("quoted_item_id")
                        if quote is not None and (
                            type(quote) is not int or not 1 <= quote <= 2**63 - 1
                        ):
                            raise ValueError("Invalid native quoted item identity")
                        composed: dict[str, Any] = {
                            "msgContent": {"type": "text", "text": args["text"]},
                            "mentions": {},
                        }
                        if quote is not None:
                            composed["quotedItemId"] = quote
                        result = await api.api_send_messages(
                            {"chatType": args["chat_type"], "chatId": args["chat_id"]},
                            [cast("T.ComposedMessage", composed)],
                        )
                    elif operation == "groups":
                        result = await api.api_list_groups(user["userId"])
                    elif operation == "new_group":
                        result = await api.api_new_group(
                            user["userId"],
                            {"displayName": args["name"], "fullName": ""},
                        )
                    elif operation == "accept_group":
                        result = await api.api_join_group(args["group_id"])
                    elif operation == "contacts":
                        result = await api.api_list_contacts(user["userId"])
                    elif operation == "join":
                        connected = False
                        result = await api.api_connect_active_user(args["link"])
                    else:
                        raise ValueError("Unsupported native operation")
                    emit(kind="result", id=command["id"], result=result)
                except Exception:
                    # Native errors can contain connection links, keys, or content.
                    emit(kind="result", id=command["id"], error="native_command_failed")
            events.cancel()
    finally:
        try:
            if api.started or secondary:
                await api.send_chat_cmd("/_stop")
        finally:
            await api.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        emit(kind="fatal", error="native_worker_failed")
        raise SystemExit(1) from None
