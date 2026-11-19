"""Supply the installed SimpleX client SDK version for startup display."""

from __future__ import annotations

import importlib.metadata

__all__ = ["build_startup_info"]


def build_startup_info() -> dict[str, object]:
    """Read package metadata without importing or starting the native client."""
    try:
        version = importlib.metadata.version("simplex-chat")
    except importlib.metadata.PackageNotFoundError:
        return {"rows": []}
    return {
        "rows": [[{"name": "client_version", "value": version, "label": "client SDK"}]]
    }
