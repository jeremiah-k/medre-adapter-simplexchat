# Native quote independent-peer evidence gate

Add a fail-closed, redacted evidence-record checker and SDK-free tests. The
checker does not execute native I/O or authorize reply capability promotion;
independent recipient-side qualification remains pending.

Bind validation to independently selected core/provider revisions, reject
contradictory or unknown JSON fields, and bound evidence reads.
