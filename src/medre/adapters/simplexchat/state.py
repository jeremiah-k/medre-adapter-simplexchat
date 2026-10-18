"""Exclusive native profile ownership and conservative queued-send deduplication."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from medre.core.events.delivery import DeliveryAttemptProvenance


class ProfileLease:
    def __init__(self, prefix: str) -> None:
        path = Path(prefix).resolve()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(
            str(path) + ".medre-owner.sqlite", timeout=0, isolation_level=None
        )
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
            raise RuntimeError(
                "SimpleX profile is owned by another process or unavailable"
            ) from None

    def close(self) -> None:
        self.db.close()


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
                    raise ValueError("SimpleX handoff provenance or content changed")
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
