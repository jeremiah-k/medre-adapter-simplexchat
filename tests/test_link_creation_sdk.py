"""Official native address and group invitation link creation contracts."""

import os
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from medre.adapters.simplexchat import cli
from medre.adapters.simplexchat.session import SimplexChatSession

pytestmark = [pytest.mark.simplexchat_sdk, pytest.mark.timeout(120)]


async def test_native_address_and_group_link_creation(tmp_path, config):
    config = replace(config, database_prefix=str(tmp_path / "native"))
    session = SimplexChatSession(config)
    await session.start(create_name="MEDRE link contract")
    try:
        record = await session.call("create_address")
        assert record["created"] is True
        link = record["link"]
        valid = isinstance(link, str) and link.startswith(("simplex:", "https://"))
        assert valid, "Native address missing"
        again = await session.call("create_address")
        assert again["created"] is False
        retained = again["link"] == link
        assert retained, "Address link changed"
        group = await session.call("new_group", name="MEDRE isolated group")
        first = await session.call("create_group_link", group_id=group["groupId"])
        assert first["created"] is True
        invitation = first["link"]
        valid = isinstance(invitation, str) and invitation.startswith(
            ("simplex:", "https://")
        )
        assert valid, "Native invitation missing"
        again = await session.call("create_group_link", group_id=group["groupId"])
        assert again["created"] is False
        retained = again["link"] == invitation
        assert retained, "Group link changed"
        recover_group = await session.call("new_group", name="MEDRE recovery group")
    finally:
        await session.stop()
    assert session.process.returncode is not None

    # The export contract requires POSIX file permissions, not Windows chmod.
    if os.name != "posix":
        return

    # The operator CLI writes the retained link only into an owner-only file.
    destination = tmp_path / "private" / "address.link"
    data = await cli.execute(
        cli.parser().parse_args(["create-address", "--out", str(destination)]),
        config,
    )
    assert data["address_created"] is False
    assert data["auto_accept"] is True
    private = link not in repr(data)
    exported = destination.read_text() == link + "\n"
    assert private and exported, "Address export contract failed"
    assert destination.stat().st_mode & 0o777 == 0o600

    # Fail after native creation, then reopen the same profile and retrieve
    # the now-existing link rather than issuing a duplicate native creation.
    group_id = str(recover_group["groupId"])
    blocked = tmp_path / "directory-not-file"
    blocked.mkdir()
    args = ["create-link", "--group-id", group_id, "--out"]
    with pytest.raises(cli.UsageError, match="correct --out"):
        await cli.execute(cli.parser().parse_args([*args, str(blocked)]), config)
    assert blocked.is_dir()
    assert not list(tmp_path.glob(".medre-link-*"))
    output = tmp_path / "recovered.link"
    result = await cli.execute(cli.parser().parse_args([*args, str(output)]), config)
    assert result["group_link_created"] is False
    assert output.stat().st_mode & 0o777 == 0o600
    # Cross-check the export after a further fresh worker start.
    check = SimplexChatSession(config)
    await check.start()
    try:
        stored = await check.call("create_group_link", group_id=int(group_id))
        assert stored["created"] is False
        exported = output.read_text() == stored["link"] + "\n"
        private = stored["link"] not in repr(result)
        assert private and exported, "Group link export contract failed"
    finally:
        await check.stop()


async def test_group_link_creation_requires_authoritative_absence():
    from simplex_chat.core import ChatAPIError

    from medre.adapters.simplexchat.worker import ensure_group_link

    api = AsyncMock()
    record = {
        "acceptMemberRole": "member",
        "connLinkContact": {
            "connShortLink": "https://simplex.chat/short#synthetic",
            "connFullLink": "simplex:/group#synthetic",
        },
    }
    api.api_get_group_link.side_effect = [
        ChatAPIError(
            "missing",
            {"type": "storeError", "storeError": {"type": "groupLinkNotFound"}},
        ),
        record,
    ]
    result = await ensure_group_link(api, 9)
    assert result == {
        "created": True,
        "link": record["connLinkContact"]["connShortLink"],
    }
    api.api_create_group_link.assert_awaited_once_with(9, "member")
    api.reset_mock()
    api.api_get_group_link.side_effect = ChatAPIError("private error")
    with pytest.raises(ChatAPIError):
        await ensure_group_link(api, 9)
    api.api_create_group_link.assert_not_awaited()


@pytest.mark.parametrize("role", ["admin", "observer", None])
async def test_group_link_export_refuses_other_roles_without_mutation(role):
    from medre.adapters.simplexchat.worker import ensure_group_link

    api = AsyncMock()
    api.api_get_group_link.return_value = {
        "acceptMemberRole": role,
        "connLinkContact": {"connFullLink": "simplex:/group#synthetic"},
    }
    with pytest.raises(RuntimeError, match="member role"):
        await ensure_group_link(api, 9)
    api.api_create_group_link.assert_not_awaited()
    api.api_set_group_link_member_role.assert_not_awaited()


async def test_existing_group_link_uses_full_link_when_short_link_is_absent():
    from medre.adapters.simplexchat.worker import ensure_group_link

    api = AsyncMock()
    api.api_get_group_link.return_value = {
        "acceptMemberRole": "member",
        "connLinkContact": {"connFullLink": "simplex:/group#synthetic"},
    }
    assert await ensure_group_link(api, 9) == {
        "created": False,
        "link": "simplex:/group#synthetic",
    }
    api.api_create_group_link.assert_not_awaited()
    api.api_set_group_link_member_role.assert_not_awaited()
