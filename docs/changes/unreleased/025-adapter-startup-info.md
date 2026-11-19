# Expose startup-info facts at the ready transition

The adapter now offers a pure-read `startup_info()` provider that core
discovers duck-typed and renders at the adapter's ready transition. It
reports the configured native user identity and the installed
`simplex-chat` client distribution version read from package metadata
(never a source literal). The hook never imports or initialises the
native client — the Python package couples startup to a
natively-downloaded library — so the version is read through
`importlib.metadata` alone and omitted when the distribution is
absent. Server mode, server address, and operator address facts are
reported only when the adapter holds them after a completed native
init; it holds none, so none are offered and none are probed. Database
keys and pairing material are never offered as facts. The hook performs
no native-process, RPC, or filesystem I/O, mutates no session state,
and never gates, blocks, or delays readiness — failure or timeout
simply yields no facts. The SDK-free fake mirrors the same duck-typed
seam.
