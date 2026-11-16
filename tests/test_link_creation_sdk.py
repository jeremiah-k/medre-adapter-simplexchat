"""Official native address and group invitation link creation contracts."""

from dataclasses import replace

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
        assert isinstance(link, str) and link.startswith(("simplex:", "https://"))
        again = await session.call("create_address")
        assert again["created"] is False
        assert again["link"] == link
        group = await session.call("new_group", name="MEDRE isolated group")
        invitation = await session.call("create_group_link", group_id=group["groupId"])
        assert isinstance(invitation, str)
        assert invitation.startswith(("simplex:", "https://"))
    finally:
        await session.stop()
    assert session.process.returncode is not None

    # The operator CLI writes the retained link only into an owner-only file.
    destination = tmp_path / "private" / "address.link"
    data = await cli.execute(
        cli.parser().parse_args(["create-address", "--out", str(destination)]),
        config,
    )
    assert data["address_created"] is False
    assert data["auto_accept"] is True
    assert link not in repr(data)
    assert destination.read_text() == link + "\n"
    assert destination.stat().st_mode & 0o777 == 0o600
