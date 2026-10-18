"""Official native profile, group history, and local queue contracts."""

from dataclasses import replace

import pytest

from medre.adapters.simplexchat.adapter import SimplexChatAdapter
from medre.adapters.simplexchat.session import SimplexChatSession

from conftest import attempt, rendered

pytestmark = [pytest.mark.simplexchat_sdk, pytest.mark.timeout(120)]


async def test_native_profile_group_pagination_and_local_queue(
    config, context, tmp_path
):
    config = replace(
        config, database_prefix=str(tmp_path / "native"), command_timeout_seconds=20
    )
    session = SimplexChatSession(config)
    await session.start(create_name="MEDRE native contract")
    try:
        await session.call("start")
        group = await session.call("new_group", name="MEDRE isolated group")
        channel = f"group:{group['groupId']}"
        assert group["membership"]["memberStatus"] == "creator"
        sent = await session.call(
            "send",
            chat_type="group",
            chat_id=group["groupId"],
            text="native queued café ✓",
        )
        native = sent[0]["chatItem"]
        assert native["content"]["msgContent"]["text"] == "native queued café ✓"
        page = await session.call(
            "history",
            chat_type="group",
            chat_id=group["groupId"],
            before=native["meta"]["itemId"],
            count=64,
        )
        assert all(
            row["meta"]["itemId"] != native["meta"]["itemId"]
            for row in page["chatItems"]
        )
    finally:
        await session.stop()
    assert session.process.returncode is not None
    config = replace(config, chats=(channel,))
    adapter = SimplexChatAdapter(config)
    await adapter.start(context[0])
    try:
        # A self-owned group proves the official local queue API only.
        # There is deliberately no assertion of a remote recipient receipt.
        result = await adapter.deliver(rendered(attempt()))
        assert result.confirmation_level == "local_queue"
        assert result.native_message_id.startswith(
            f"{adapter.codec.profile}:user:1:{channel}:item:"
        )
        assert int(result.native_message_id.rsplit(":", 1)[-1]) > 0
        assert not context[0].admit_inbound.called
    finally:
        await adapter.stop()

    replacement = SimplexChatAdapter(config)
    await replacement.start(context[0])
    try:
        duplicate = await replacement.deliver(rendered(attempt()))
        assert duplicate.native_message_id == result.native_message_id
        history = await replacement.session.call(
            "history", chat_type="group", chat_id=group["groupId"], count=64
        )
        assert (
            sum(
                row.get("content", {}).get("msgContent", {}).get("text")
                == "Rendered message"
                for row in history["chatItems"]
            )
            == 1
        )
        process = replacement.session.process
        process.kill()
        await process.wait()
        from conftest import wait_until

        await wait_until(lambda: replacement.state == "failed")
    finally:
        await replacement.stop()
    # A dead worker must release profile ownership for the next explicit start.
    third = SimplexChatAdapter(config)
    await third.start(context[0])
    await third.stop()
