"""Startup-info facts from state the SimpleX Chat adapter already holds.

Core's runtime duck-types an optional ``startup_info()`` callable on a
built adapter and renders the returned scalar facts at the adapter's
ready transition (see the adapter-startup-info specification in the
core distribution).  This module shapes the facts this adapter holds at
that transition — the configured native user identity and the installed
``simplex-chat`` client distribution version — into the plain mapping
the contract expects.

Pure read: the provider performs no native-process, RPC, or filesystem
I/O, acquires no resources, and mutates no session state, so a
synchronous callable is safe to run on any worker thread.  It must
never construct or initialise the native client: importing the Python
``simplex_chat`` package couples startup to a natively-downloaded
library, so this module only reads local package metadata via
``importlib.metadata`` — never a source literal (the repository's
pin-hygiene guard keeps pin values in ``pyproject.toml`` only) — and
omits the version when the distribution is absent.  Server mode and
address facts are reported only when the adapter holds them after a
completed native init; this adapter holds no such facts, so none are
offered, and none are ever probed: startup info must never gate,
block, or delay the native session.  Secret material — database keys,
pairing and connection material — is never offered as a fact.  This
module imports nothing from ``medre.core``: the returned mapping is
plain JSON-like data, validated and redacted by core at the render
boundary.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any

from medre.adapters.simplexchat.config import SimplexChatConfig

__all__ = ["build_startup_info"]


def _installed_client_version() -> str | None:
    """Return the installed ``simplex-chat`` client version, or ``None``.

    Reads local package metadata via ``importlib.metadata`` without
    importing the package — importing it would couple the read to a
    natively-downloaded library.  The value is never a source literal.
    ``None`` means the distribution is not installed (fake-only
    environments) and the fact is omitted.
    """
    try:
        return importlib.metadata.version("simplex-chat")
    except importlib.metadata.PackageNotFoundError:
        return None


def build_startup_info(config: SimplexChatConfig) -> dict[str, Any]:
    """Build the startup-info mapping from held state.

    The configured native user identity forms the first row and the
    installed client version the second, per the contract's row
    guidance.  The adapter holds no server mode, server address, or
    operator address facts, so none are offered here, and none are ever
    probed from the native client.
    """
    account: list[dict[str, Any]] = [
        {"name": "user_id", "value": config.user_id, "label": "user"}
    ]
    rows: list[list[dict[str, Any]]] = [account]
    client_version = _installed_client_version()
    if client_version:
        rows.append(
            [
                {
                    "name": "client_version",
                    "value": client_version,
                    "label": "client",
                }
            ]
        )
    return {"rows": rows}
