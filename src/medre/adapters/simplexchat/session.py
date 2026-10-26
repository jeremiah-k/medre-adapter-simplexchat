"""Bound native calls and abort the owned process on uncertain outcomes."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from .config import SimplexChatConfig
from .state import ProfileLease

_STOP_TIMEOUT = 5.0
_DRAIN_TIMEOUT = 2.0


class SessionError(RuntimeError):
    """Native session failure; the command may have reached the worker."""


class SessionUnavailable(SessionError):
    """A call refused before any command bytes reached the worker."""


class SimplexChatSession:
    def __init__(self, config: SimplexChatConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.lease: ProfileLease | None = None
        self.ready: asyncio.Future[Any] | None = None
        self.pending: dict[str, asyncio.Future[Any]] = {}
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.stop_lock = asyncio.Lock()
        self.closed = False
        self._native_reaped = False
        self.failed = False
        self.used = False

    async def start(self, *, create_name: str | None = None) -> None:
        if self.used:
            raise SessionError("SimpleX sessions cannot be reused")
        self.used = True
        self.lease = ProfileLease(self.config.database_prefix)
        ready = asyncio.get_running_loop().create_future()
        self.ready = ready
        environment = os.environ.copy()
        if self.config.native_cache_dir:
            environment["XDG_CACHE_HOME"] = self.config.native_cache_dir
        if self.config.libs_dir:
            environment["SIMPLEX_LIBS_DIR"] = self.config.libs_dir
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "medre.adapters.simplexchat.worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=environment,
                limit=4 * 1024 * 1024,
                start_new_session=sys.platform != "win32",
            )
            self.process = process
            self.reader = asyncio.create_task(self._read())
            stdin = process.stdin
            if stdin is None:
                raise SessionUnavailable("SimpleX session is unavailable")
            async with asyncio.timeout(self.config.startup_timeout_seconds):
                stdin.write(
                    (
                        json.dumps({**asdict(self.config), "create_name": create_name})
                        + "\n"
                    ).encode()
                )
                await stdin.drain()
                user_id = await asyncio.shield(ready)
            if user_id != self.config.user_id:
                raise SessionError(
                    "SimpleX active user differs from configured user_id; initialize or select the intended profile explicitly"
                )
        except BaseException:
            await self.stop()
            raise

    async def _read(self) -> None:
        try:
            reader = self.process
            stdout = reader.stdout if reader is not None else None
            ready = self.ready
            if stdout is None or ready is None:
                raise SessionError("SimpleX session is unavailable")
            while line := await stdout.readline():
                if not line.startswith(b"SXRPC "):
                    continue
                record = json.loads(line[6:])
                if record["kind"] == "ready":
                    ready.set_result(record["user_id"])
                elif record["kind"] == "wake":
                    self.wake.set()
                elif record["kind"] == "result":
                    future = self.pending.get(record.get("id"))
                    if future is not None and not future.done():
                        if "error" in record:
                            future.set_exception(
                                SessionError("SimpleX native command rejected")
                            )
                        else:
                            future.set_result(record["result"])
                else:
                    raise SessionError("SimpleX native worker failed")
        except Exception:
            pass
        finally:
            self.failed = True
            for future in [self.ready, *self.pending.values()]:
                if future is not None and not future.done():
                    future.set_exception(
                        SessionError("SimpleX native worker disconnected")
                    )
            self.wake.set()

    async def call(self, operation: str, **args: Any) -> Any:
        async with self.lock:
            if (
                self.failed
                or self.process is None
                or self.process.returncode is not None
            ):
                raise SessionUnavailable("SimpleX session is unavailable")
            command = uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self.pending[command] = future
            try:
                async with asyncio.timeout(self.config.command_timeout_seconds):
                    stdin = self.process.stdin
                    if stdin is None:
                        raise SessionUnavailable("SimpleX session is unavailable")
                    stdin.write(
                        (
                            json.dumps({"op": operation, "id": command, "args": args})
                            + "\n"
                        ).encode()
                    )
                    await stdin.drain()
                    return await future
            except (
                TimeoutError,
                asyncio.CancelledError,
                BrokenPipeError,
                ConnectionResetError,
            ):
                await self.stop()
                task = asyncio.current_task()
                if task is None or task.cancelling():
                    raise
                raise SessionError(
                    "SimpleX native command outcome is uncertain"
                ) from None
            finally:
                self.pending.pop(command, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()

    def _kill_owned(self) -> None:
        if self.process is None:
            return
        if sys.platform == "win32":
            if self.process.returncode is None:
                self.process.kill()
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        """Serialize cleanup and retain ownership until the native worker is reaped.

        Failed or interrupted cleanup leaves the profile fenced for another stop
        attempt. Closed sessions never signal a stale, potentially reused PID.
        """
        async with self.stop_lock:
            if self.closed:
                return
            self.failed = True
            await self._stop_owned()

    async def _stop_owned(self) -> None:
        """Bound one cleanup attempt without releasing a possibly live native owner."""
        process = self.process
        reaped = process is None or self._native_reaped
        try:
            if process is not None and not self._native_reaped:
                if process.returncode is None:
                    stdin = process.stdin
                    if stdin is None:
                        raise SessionError("SimpleX session is unavailable")
                    try:
                        async with asyncio.timeout(_STOP_TIMEOUT):
                            stdin.write(b'{"op":"close","id":"close"}\n')
                            await stdin.drain()
                            stdin.close()
                            await process.wait()
                    except (TimeoutError, BrokenPipeError, ConnectionResetError):
                        # A descendant can keep stdout open after the leader
                        # exits. Kill the owned group even when returncode is set.
                        self._kill_owned()
                        await asyncio.wait_for(process.wait(), _DRAIN_TIMEOUT)
                if self.reader is not None:
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(self.reader), _DRAIN_TIMEOUT
                        )
                    except TimeoutError:
                        self._kill_owned()
                        await asyncio.wait_for(self.reader, _DRAIN_TIMEOUT)
        finally:
            try:
                if process is not None and not self._native_reaped:
                    self._kill_owned()
                    if process.returncode is None:
                        await asyncio.wait_for(process.wait(), _DRAIN_TIMEOUT)
                    reaped = process.returncode is not None
                    self._native_reaped = reaped
            finally:
                if self.reader is not None and not self.reader.done():
                    self.reader.cancel()
                if (
                    self.ready is not None
                    and self.ready.done()
                    and not self.ready.cancelled()
                ):
                    self.ready.exception()
                if self.reader is not None:
                    await asyncio.gather(self.reader, return_exceptions=True)
                if reaped:
                    if self.lease is not None:
                        self.lease.close()
                        self.lease = None
                    self.closed = True
