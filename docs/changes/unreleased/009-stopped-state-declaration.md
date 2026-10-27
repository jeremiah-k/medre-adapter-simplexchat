# Declare SimpleX stopped-state files for the maintenance snapshot

Ship a provider-owned stopped-state declaration module and reference it from
the adapter spec through the optional `stopped_state_declaration` symbol. The
declaration maps one configured profile to the exact files a stopped-state
capture must select — the handoff and inbox ledgers, the profile namespace
database, the ownership guard inode, and both native profile databases — and
reuses the adapter's own flock guard as the local exclusion primitive without
seizing or starting anything. A configured `database_key` marks the encrypted
profile unsupported for automatic planning until a coherent provider export
includes its native state and adapter journals, and the
native cache directory stays recorded as disposable rather than selected.
Consumers and the trust-boundary contract live in
`medre.maintenance.stopped_declare` on the core side; a distribution without a
declaration is reported as unsupported there rather than guessed.
