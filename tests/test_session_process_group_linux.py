"""Linux group lifetime with teardown owned by an isolated child subreaper."""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from types import SimpleNamespace

import pytest

from medre.adapters.simplexchat.session import SimplexChatSession


async def _finish_guardian(guardian, started=None):
    """Retain cleanup ownership through cancellation until all helper pipes drain.

    Cancelling this caller cannot cancel communication or kill the process that
    still owns descendant reaping. The helper enforces its own bounded shutdown.
    """

    async def exchange():
        """Close control and reap the owner even if its I/O operation fails."""
        try:
            return await guardian.communicate(b"stop\n")
        finally:
            if guardian.stdin is not None:
                guardian.stdin.close()
            await guardian.wait()

    communication = asyncio.create_task(exchange())
    if started is not None:
        started.set()
    try:
        result = await asyncio.shield(communication)
    except asyncio.CancelledError:
        while not communication.done():
            try:
                await asyncio.shield(communication)
            except asyncio.CancelledError:
                continue
        _, errors = communication.result()
        assert guardian.returncode == 0, errors.decode(errors="replace")
        raise
    _, errors = result
    assert guardian.returncode == 0, errors.decode(errors="replace")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process-group semantics")
@pytest.mark.parametrize("cleanup", ["normal", "cancelled", "timeout", "io_error"])
async def test_reaped_worker_leader_is_not_proof_of_group_exit(cleanup, monkeypatch):
    """Leader reaping, caller timeout and cancellation cannot abandon a descendant."""
    descendant = "import signal; signal.pause()"
    leader = textwrap.dedent(f"""
        import signal, subprocess, sys
        def deadline(signum, frame):
            raise TimeoutError('ownership handoff deadline')
        child = subprocess.Popen(
            [sys.executable, '-c', {descendant!r}],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        transferred = False
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, 3)
        try:
            print(child.pid, flush=True)
            transferred = sys.stdin.buffer.read(1) == b'x'
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if not transferred:
                child.kill()
                child.wait()
        if not transferred:
            raise RuntimeError('descendant ownership was not transferred')
        """)
    # Only a successful, acknowledged handoff permits the leader to exit
    # without reaping its child. The guardian then owns that unreaped PID.
    script = textwrap.dedent(f"""
        import ctypes, json, os, signal, subprocess, sys
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, *([ctypes.c_ulong] * 4)]
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'cannot establish test subreaper')
        def deadline(signum, frame):
            raise TimeoutError('probe control deadline')
        worker = None
        child = None
        signal.signal(signal.SIGALRM, deadline)
        try:
            worker = subprocess.Popen(
                [sys.executable, '-c', {leader!r}],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                start_new_session=True,
            )
            signal.setitimer(signal.ITIMER_REAL, 5)
            child = int(worker.stdout.readline())
            worker.stdin.write(b'x')
            worker.stdin.flush()
            status = worker.wait(timeout=4)
            running = os.waitid(os.P_PID, child, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
            print(json.dumps({{'pid': worker.pid, 'child': child, 'status': status, 'running': running}}), flush=True)
            sys.stdin.buffer.readline()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if worker is not None:
                # EOF aborts an unacknowledged handoff; that leader cleans its
                # own child. A successful handoff leaves the child unreaped
                # under this subreaper, reserving its PID until waitpid.
                worker.stdin.close()
                status = worker.wait(timeout=4)
                if status == 0 and child is not None:
                    try:
                        os.waitid(os.P_PID, child, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    except ChildProcessError:
                        pass  # No retained child identity means no signal authority.
                    else:
                        os.kill(child, signal.SIGKILL)
                        os.waitpid(child, 0)
                worker.stdout.close()
        """)
    guardian = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    group = None
    try:
        assert guardian.stdout is not None
        ready = await asyncio.wait_for(guardian.stdout.readline(), timeout=5)
        record = json.loads(ready)
        assert record["status"] == 0
        assert record["running"] is True
        assert record["child"] != record["pid"]
        group = SimpleNamespace(pid=record["pid"])
        assert SimplexChatSession._owned_group_active(group)
    finally:
        if cleanup == "cancelled":
            started = asyncio.Event()
            finishing = asyncio.create_task(_finish_guardian(guardian, started))
            await started.wait()
            finishing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await finishing
        elif cleanup == "io_error":

            async def failed_exchange(*args):
                """Fail communication after startup without releasing child ownership."""
                raise OSError("controlled communication failure")

            monkeypatch.setattr(guardian, "communicate", failed_exchange)
            with pytest.raises(OSError, match="controlled communication"):
                await _finish_guardian(guardian)
            assert guardian.returncode == 0
        elif cleanup == "timeout":
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0):
                    await _finish_guardian(guardian)
        else:
            await _finish_guardian(guardian)
    assert group is not None
    assert not SimplexChatSession._owned_group_active(group)
