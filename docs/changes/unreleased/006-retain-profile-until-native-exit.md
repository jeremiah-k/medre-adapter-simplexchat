# Retain profile ownership until confirmed native shutdown

Keep the profile lease and owned session when native process reaping times out
or cleanup is cancelled. Refuse native calls during shutdown, serialize
concurrent stop requests and permit a bounded cleanup retry. Completed cleanup
is idempotent and does not signal a stale process identifier. The adapter
reports failed health while preserving its session reference for cleanup.

Accepted and uncertain handoff ledger records are retained. Package version,
configuration, native payload fields and delivery confirmation are unchanged.
