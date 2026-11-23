"""Isolated checks of the live campaign boundaries without a native server."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/qualify_two_profile_recipient.py"
spec = importlib.util.spec_from_file_location("simplex_campaign_helpers", PATH)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_local_server_requires_numeric_loopback():
    for host in ("127.0.0.1", "[::1]"):
        gate._validate_local_server(f"smp://fingerprint=@{host}:5223")
    for address in (
        "smp://fingerprint=@example.com",
        "smp://fingerprint=@192.0.2.7",
        "smp://fingerprint=@127.0.0.1.evil.test",
        "smp://fingerprint=@localhost",
        "https://127.0.0.1/short-link",
        "smp://fingerprint=@127.0.0.1/other-path",
    ):
        with pytest.raises(ValueError, match="loopback"):
            gate._validate_local_server(address)


def test_harness_requires_a_native_full_link_not_https():
    assert gate._full_link(
        {"connLinkContact": {"connFullLink": "simplex:/contact#native"}}
    ).startswith("simplex:/")
    with pytest.raises(RuntimeError, match="full group link"):
        gate._full_link(
            {"connLinkContact": {"connFullLink": "https://example.test/hosted"}}
        )


def test_sender_identity_is_mapped_to_recipient_local_group_ids():
    group = lambda number, member: {
        "groupId": number,
        "membership": {"memberId": member},
    }
    received = [group(6, "recipient-target"), group(8, "recipient-sibling")]
    sender = {
        "group:6": gate._sha256("sender-target"),
        "group:8": gate._sha256("sender-sibling"),
    }
    rosters = gate._roster(received, sender)
    assert rosters["group:6"] == sorted(
        [gate._sha256("recipient-target"), sender["group:6"]]
    )
    assert rosters["group:8"] == sorted(
        [gate._sha256("recipient-sibling"), sender["group:8"]]
    )


def test_checkpoint_concurrent_commits_preserve_both_streams(tmp_path):
    context = object.__new__(gate.DurableHarnessContext)
    context.checkpoints_path = tmp_path / "checkpoints.json"
    context._checkpoint_lock = asyncio.Lock()

    async def run():
        await asyncio.gather(
            context._commit("group:5", "cursor-5", "{}"),
            context._commit("group:7", "cursor-7", "{}"),
        )

    asyncio.run(run())
    saved = json.loads(context.checkpoints_path.read_text())
    assert saved == {
        "group:5": {"cursor": "cursor-5", "metadata_json": "{}"},
        "group:7": {"cursor": "cursor-7", "metadata_json": "{}"},
    }
    assert not (tmp_path / "checkpoints.json.tmp").exists()


def test_source_pin_checks_actual_worktree_and_untracked_importable_files(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "src").mkdir()
    tracked = repo / "src" / "mod.py"
    tracked.write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "src/mod.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Initial",
        ],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    assert asyncio.run(gate._source_unchanged(repo, head, "src"))
    tracked.write_text("VALUE = 2\n")
    assert not asyncio.run(gate._source_unchanged(repo, head, "src"))
    tracked.write_text("VALUE = 1\n")
    (repo / "src" / "untracked.py").write_text("VALUE = 3\n")
    assert not asyncio.run(gate._source_unchanged(repo, head, "src"))


def test_redrive_counter_sees_native_request_even_if_result_reused():
    class Session:
        async def call(self, operation: str, **kwargs):
            return None

    class Adapter:
        session = Session()

        async def deliver(self, rendering):
            await self.session.call("send", text="unobserved")
            return "reused"

    adapter = Adapter()
    result, sends = asyncio.run(gate._redrive_with_send_count(adapter, object()))
    assert result == "reused" and sends == 1
    assert adapter.session.call.__func__ is Session.call


def test_fresh_retained_history_requires_exact_item_and_quote(monkeypatch):
    reply = {"item_id": 44, "text_sha256": "4" * 64}
    parent = {"item_id": 21, "text_sha256": "1" * 64}
    actual = {
        "channel": "group:6",
        "item_id": 44,
        "text_sha256": "4" * 64,
        "quoted_item_id": 21,
        "quoted_text_sha256": "1" * 64,
    }
    monkeypatch.setattr(gate, "_native_view", lambda raw: raw)
    assert gate._fresh_reply_retained(
        [actual], channel="group:6", reply=reply, parent=parent
    )
    for key, value in (
        ("channel", "group:8"),
        ("item_id", 45),
        ("quoted_item_id", 30),
        ("quoted_text_sha256", "f" * 64),
    ):
        mutated = {**actual, key: value}
        assert not gate._fresh_reply_retained(
            [mutated], channel="group:6", reply=reply, parent=parent
        )
