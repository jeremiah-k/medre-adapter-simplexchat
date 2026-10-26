"""The Linux worker inherits profile fencing when its Python owner is killed."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from medre.adapters.simplexchat.state import ProfileLease


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux file-description guard"
)
def test_native_worker_owns_guard_after_abrupt_bridge_death(tmp_path):
    """SIGKILL of the bridge may not admit a second owner while worker survives."""
    prefix = str(tmp_path / "simplex-profile")
    owner_code = textwrap.dedent("""
        import signal, subprocess, sys
        from medre.adapters.simplexchat.state import ProfileLease
        lease = ProfileLease(sys.argv[1])
        worker = subprocess.Popen(
            [sys.executable, '-c', 'import signal; signal.pause()'],
            pass_fds=(lease.guard_fd,),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print(worker.pid, flush=True)
        signal.pause()
        """)
    # The isolated guardian adopts and reaps the orphan. The pytest process
    # never changes its own subreaper policy or leaves cleanup to host init.
    script = textwrap.dedent(f"""
        import ctypes, os, signal, subprocess, sys
        from medre.adapters.simplexchat.state import ProfileLease
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, *([ctypes.c_ulong] * 4)]
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'cannot establish test subreaper')
        def deadline(signum, frame):
            raise TimeoutError('profile ownership probe deadline')
        owner = None
        worker_pid = None
        signal.signal(signal.SIGALRM, deadline)
        try:
            owner = subprocess.Popen(
                [sys.executable, '-c', {owner_code!r}, sys.argv[1]],
                stdout=subprocess.PIPE, stderr=sys.stderr, start_new_session=True,
            )
            signal.setitimer(signal.ITIMER_REAL, 5)
            worker_pid = int(owner.stdout.readline())
            owner.kill()
            assert owner.wait(timeout=3) == -signal.SIGKILL
            # WNOWAIT reserves the adopted child's identity, even if it exited.
            assert os.waitid(os.P_PID, worker_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
            try:
                contender = ProfileLease(sys.argv[1])
            except RuntimeError as exc:
                assert 'owned' in str(exc)
            else:
                contender.close()
                raise AssertionError('surviving native worker lost profile fencing')
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if owner is not None:
                if owner.returncode is None:
                    # The unreaped leader reserves this process-group ID.
                    os.killpg(owner.pid, signal.SIGKILL)
                    owner.wait(timeout=3)
                owner.stdout.close()
            if worker_pid is not None:
                try:
                    os.waitid(os.P_PID, worker_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                except ChildProcessError:
                    pass
                else:
                    os.kill(worker_pid, signal.SIGKILL)
            while True:
                try:
                    os.waitpid(-1, 0)
                except ChildProcessError:
                    break
        ProfileLease(sys.argv[1]).close()
        """)
    result = subprocess.run(
        [sys.executable, "-c", script, prefix],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux file-description guard"
)
def test_guard_is_released_after_native_worker_exits(tmp_path):
    """Ordinary closed sessions do not leave persistent file-lock ownership."""
    prefix = str(tmp_path / "profile")
    lease = ProfileLease(prefix)
    descriptor = lease.guard_fd
    assert descriptor is not None
    assert not os.get_inheritable(descriptor)
    with pytest.raises(RuntimeError, match="owned"):
        ProfileLease(prefix)
    lease.close()
    new = ProfileLease(prefix)
    new.close()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux file-description guard"
)
def test_closing_bridge_lease_does_not_unlock_surviving_worker(tmp_path):
    """An ordinary close releases only the parent's shared lock descriptor."""
    prefix = str(tmp_path / "profile")
    lease = ProfileLease(prefix)
    try:
        worker = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
            pass_fds=(lease.guard_fd,),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            lease.close()
            with pytest.raises(RuntimeError, match="owned"):
                ProfileLease(prefix)
        finally:
            assert worker.stdin is not None
            worker.stdin.close()
            try:
                worker.wait(timeout=5)
            except BaseException:
                worker.kill()
                worker.wait(timeout=5)
                raise
    finally:
        lease.close()
    ProfileLease(prefix).close()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux file-description guard"
)
async def test_native_start_passes_the_owned_guard_descriptor(config, monkeypatch):
    """The real session spawn path must pass the same file-description guard."""
    from medre.adapters.simplexchat import session as boundary
    from medre.adapters.simplexchat.session import SimplexChatSession

    seen = []

    async def refused_spawn(*args, **kwargs):
        assert session.lease is not None
        assert kwargs.get("pass_fds") == (session.lease.guard_fd,)
        os.fstat(session.lease.guard_fd)
        seen.append(kwargs.get("pass_fds"))
        raise RuntimeError("fixture native worker unavailable")

    monkeypatch.setattr(boundary.asyncio, "create_subprocess_exec", refused_spawn)
    session = SimplexChatSession(config)
    with pytest.raises(RuntimeError, match="fixture native worker"):
        await session.start()
    assert len(seen) == 1
    assert isinstance(seen[0], tuple) and len(seen[0]) == 1
    assert session.closed and session.lease is None
