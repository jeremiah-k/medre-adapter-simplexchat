"""Exercise real process ownership with an SDK boundary stub."""

import asyncio
from dataclasses import replace

import pytest

from medre.adapters.simplexchat.session import SessionError, SimplexChatSession
from medre.adapters.simplexchat.state import ProfileLease


@pytest.fixture
def stub_sdk(tmp_path, monkeypatch):
    path = tmp_path / "sdk"
    path.mkdir()
    (path / "simplex_chat.py").write_text("""
import asyncio
class MigrationConfirmation:
    YES_UP = 1
class SqliteDb:
    def __init__(self, prefix, key):
        self.key = key
class ChatApi:
    started = False
    @classmethod
    async def init(cls, db, confirm):
        if db.key == 'hang':
            await asyncio.Event().wait()
        if db.key == 'bad-user':
            cls.uid = 2
        else:
            cls.uid = 1
        return cls()
    async def send_chat_cmd(self, command):
        return {"type": "chatStarted" if "start" in command else "chatStopped"}
    async def api_get_active_user(self):
        return {'userId': self.uid}
    async def start_chat(self):
        self.started = True
    async def stop_chat(self):
        self.started = False
    async def close(self):
        pass
    async def recv_chat_event(self, **kwargs):
        await asyncio.Event().wait()
    async def api_list_groups(self, user):
        await asyncio.Event().wait()
""")
    monkeypatch.setenv("PYTHONPATH", str(path))


async def test_command_deadline_reaps_process_and_releases_lease(config, stub_sdk):
    session = SimplexChatSession(replace(config, command_timeout_seconds=0.05))
    await session.start()
    process = session.process
    with pytest.raises(SessionError, match="uncertain"):
        await session.call("groups")
    assert process.returncode is not None
    ProfileLease(config.database_prefix).close()
    await session.stop()


async def test_startup_deadline_reaps_stalled_initialization(config, stub_sdk):
    session = SimplexChatSession(
        replace(config, database_key="hang", startup_timeout_seconds=0.2)
    )
    with pytest.raises(TimeoutError):
        await session.start()
    assert session.process.returncode is not None
    assert session.lease is None


async def test_wrong_active_user_fails_closed_and_releases_profile(config, stub_sdk):
    session = SimplexChatSession(replace(config, database_key="bad-user"))
    with pytest.raises(SessionError, match="active user"):
        await session.start()
    assert session.process.returncode is not None
    ProfileLease(config.database_prefix).close()


async def test_cancelled_call_terminates_native_owner(config, stub_sdk):
    session = SimplexChatSession(config)
    await session.start()
    call = asyncio.create_task(session.call("groups"))
    # Let the worker confirm receipt by observing the pending request.
    from conftest import wait_until

    await wait_until(lambda: bool(session.pending))
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert session.process.returncode is not None
    await session.stop()


@pytest.mark.skipif(
    __import__("sys").platform == "win32", reason="POSIX process-group ownership"
)
async def test_shutdown_reaps_descendants_that_keep_stdout_open(
    config, stub_sdk, tmp_path, monkeypatch
):
    import os
    from medre.adapters.simplexchat import session as boundary

    monkeypatch.setattr(boundary, "_STOP_TIMEOUT", 0.05)
    monkeypatch.setattr(boundary, "_DRAIN_TIMEOUT", 1.0)
    module = tmp_path / "sdk" / "simplex_chat.py"
    source = module.read_text().replace(
        "        self.started = True",
        """        self.started = True
        import subprocess, sys
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL)
        from pathlib import Path
        Path(CHILD_PID).write_text(str(child.pid))""",
    )
    source = "CHILD_PID = " + repr(str(tmp_path / "child.pid")) + "\n" + source
    module.write_text(source)
    session = SimplexChatSession(config)
    await session.start()
    try:
        await session.call("start")
        child = int((tmp_path / "child.pid").read_text())
        assert os.getpgid(child) == session.process.pid
        with pytest.raises(SessionError, match="process group"):
            await asyncio.wait_for(session.stop(), 5)
        assert session.reader.done()
        assert session.process.returncode is not None
        with pytest.raises(RuntimeError, match="owned"):
            ProfileLease(config.database_prefix)
    finally:
        # Fixture cleanup kills the test-owned descendant by its known PID;
        # production deliberately never signals a reused group leader PID.
        if (tmp_path / "child.pid").exists():
            try:
                os.kill(int((tmp_path / "child.pid").read_text()), 9)
            except ProcessLookupError:
                pass
        if session.lease is not None:
            session.lease.close()


async def test_shutdown_deadline_includes_stdin_backpressure(
    config, stub_sdk, monkeypatch
):
    from medre.adapters.simplexchat import session as boundary
    from conftest import wait_until

    monkeypatch.setattr(boundary, "_STOP_TIMEOUT", 0.05)
    monkeypatch.setattr(boundary, "_DRAIN_TIMEOUT", 1.0)
    session = SimplexChatSession(config)
    await session.start()
    request = asyncio.create_task(session.call("groups"))
    try:
        await wait_until(lambda: bool(session.pending))
        session.process.stdin.write(b"x" * (1024 * 1024))
        await asyncio.wait_for(session.stop(), 5)
        assert session.process.returncode is not None
    finally:
        await session.stop()
        await asyncio.gather(request, return_exceptions=True)


async def test_reap_failure_retains_profile_until_confirmed_shutdown(
    config, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    session = SimplexChatSession(config)
    session.lease = ProfileLease(config.database_prefix)
    session.process = SimpleNamespace(
        pid=999999999,
        returncode=None,
        stdin=SimpleNamespace(
            write=lambda _: None, drain=AsyncMock(), close=lambda: None
        ),
        wait=AsyncMock(side_effect=TimeoutError),
    )
    monkeypatch.setattr(session, "_kill_owned", lambda: None)
    with pytest.raises(TimeoutError):
        await session.stop()
    assert session.lease is not None
    assert session.failed and not session.closed
    with pytest.raises(RuntimeError, match="owned"):
        ProfileLease(config.database_prefix)
    session.process.returncode = 0
    await session.stop()
    assert session.lease is None and session.closed
    ProfileLease(config.database_prefix).close()
