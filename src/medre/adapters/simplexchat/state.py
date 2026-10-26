"""Exclusive native profile ownership and conservative queued-send deduplication."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from uuid import uuid4

from medre.core.events.delivery import DeliveryAttemptProvenance


class ProfileLease:
    """Fence one native profile and retain its stable handoff namespace.

    Linux workers share an inherited open file description with the bridge.
    Closing the bridge's handle cannot revoke a surviving worker's lock.
    """

    def __init__(self, prefix: str) -> None:
        """Acquire both Linux fencing and the SQLite namespace before native use."""
        path = Path(prefix).resolve()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.guard_fd: int | None = None
        if sys.platform.startswith("linux"):
            # The native worker inherits this open-file-description lock. If
            # the Python bridge dies, a surviving worker still fences profile
            # admission even though the bridge's SQLite transaction is gone.
            import fcntl

            guard = str(path) + ".medre-owner.lock"
            descriptor = os.open(
                guard,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise RuntimeError("SimpleX ownership guard is not a regular file")
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise RuntimeError(
                    "SimpleX profile is owned by another process or unavailable"
                ) from None
            self.guard_fd = descriptor
        try:
            self.db = sqlite3.connect(
                str(path) + ".medre-owner.sqlite", timeout=0, isolation_level=None
            )
        except BaseException:
            self._release_guard()
            raise
        try:
            Path(str(path) + ".medre-owner.sqlite").chmod(0o600)
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK(id=1), namespace TEXT NOT NULL)"
            )
            self.db.execute(
                "INSERT OR IGNORE INTO profile VALUES (1, ?)", (str(uuid4()),)
            )
            self.db.execute("BEGIN EXCLUSIVE")
            self.namespace = self.db.execute(
                "SELECT namespace FROM profile WHERE id=1"
            ).fetchone()[0]
        except Exception:
            self.db.close()
            self._release_guard()
            raise RuntimeError(
                "SimpleX profile is owned by another process or unavailable"
            ) from None

    def _release_guard(self) -> None:
        """Close this holder only; LOCK_UN would revoke every inherited holder.

        The guard file remains in place so subsequent owners lock the same
        inode. The kernel releases fencing after the last shared handle closes.
        """
        descriptor, self.guard_fd = self.guard_fd, None
        if descriptor is not None:
            os.close(descriptor)

    def close(self) -> None:
        """Release this owner without unlocking a surviving inherited worker."""
        try:
            self.db.close()
        finally:
            self._release_guard()


class HandoffLedger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS handoffs (outbox TEXT NOT NULL, attempt INTEGER NOT NULL, request TEXT NOT NULL, native_id TEXT, PRIMARY KEY(outbox, attempt))"
        )

    def reserve(
        self, provenance: DeliveryAttemptProvenance, channel: str, text: str
    ) -> tuple[bool, str | None]:
        """Reserve unsent bytes or recover the same attempt's accepted native fact.

        Accepted provenance and destination stay immutable across rendering
        upgrades. A reservation without native acceptance never authorizes a
        second send, even when only the newly rendered text differs.
        """
        import msgspec

        request = json.dumps(
            [msgspec.to_builtins(provenance), channel, text],
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.db:
            row = self.db.execute(
                "SELECT request,native_id FROM handoffs WHERE outbox=? AND attempt=?",
                (provenance.outbox_id, provenance.attempt_number),
            ).fetchone()
            if row:
                if row[0] != request:
                    # A completed native handoff is authoritative for this
                    # exact attempt. A renderer upgrade may change its text,
                    # but must never trigger a second send. Unresolved
                    # reservations remain bound to their original bytes.
                    if row[1] is None or (
                        json.loads(row[0])[:2] != json.loads(request)[:2]
                    ):
                        raise ValueError(
                            "SimpleX handoff provenance or content changed"
                        )
                return False, row[1]
            unresolved = self.db.execute(
                "SELECT 1 FROM handoffs WHERE outbox=?",
                (provenance.outbox_id,),
            ).fetchone()
            if unresolved:
                raise ValueError(
                    "SimpleX outbox has a prior handoff; inspect the native profile before retrying"
                )
            self.db.execute(
                "INSERT INTO handoffs VALUES (?,?,?,NULL)",
                (provenance.outbox_id, provenance.attempt_number, request),
            )
            return True, None

    def release(self, provenance: DeliveryAttemptProvenance) -> None:
        """Undo only an unsubmitted reservation, never an accepted handoff."""
        with self.db:
            self.db.execute(
                "DELETE FROM handoffs WHERE outbox=? AND attempt=? "
                "AND native_id IS NULL",
                (provenance.outbox_id, provenance.attempt_number),
            )

    def accepted(self, provenance: DeliveryAttemptProvenance, native_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE handoffs SET native_id=? WHERE outbox=? AND attempt=? AND native_id IS NULL",
                (native_id, provenance.outbox_id, provenance.attempt_number),
            )

    def close(self) -> None:
        self.db.close()


class InboxLedger:
    """Retain processed native IDs independently of native timestamp ordering."""

    def __init__(self, path: Path, profile: str) -> None:
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS admitted (profile TEXT, channel TEXT, item INTEGER, PRIMARY KEY(profile, channel, item))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS baselines (profile TEXT, channel TEXT, PRIMARY KEY(profile, channel))"
        )
        self.profile = profile

    def contains(self, channel: str, item: int) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM admitted WHERE profile=? AND channel=? AND item=?",
                (self.profile, channel, item),
            ).fetchone()
            is not None
        )

    def admitted(self, channel: str, item: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO admitted VALUES (?,?,?)",
                (self.profile, channel, item),
            )

    def admitted_many(self, channel: str, items: list[int]) -> None:
        """Commit one baseline page atomically rather than fsync per item."""
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO admitted VALUES (?,?,?)",
                ((self.profile, channel, item) for item in items),
            )

    def initialized(self, channel: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM baselines WHERE profile=? AND channel=?",
                (self.profile, channel),
            ).fetchone()
            is not None
        )

    def initialize(self, channel: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO baselines VALUES (?,?)", (self.profile, channel)
            )

    def close(self) -> None:
        self.db.close()
