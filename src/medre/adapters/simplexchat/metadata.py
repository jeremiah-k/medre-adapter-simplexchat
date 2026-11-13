"""Versioned native facts and attribution, independent of the SDK."""

from collections.abc import Mapping
from typing import Any

from medre.adapters.metadata import current_namespace, versioned_namespace


def native_namespace(data: Mapping[str, Any]) -> Mapping[str, Any]:
    return current_namespace(data, "simplexchat", 1)


def versioned(data: Mapping[str, Any]) -> Mapping[str, Any]:
    return versioned_namespace(data, "simplexchat")


def project_attribution(
    data: Mapping[str, Any],
    *,
    source_transport_id: str | None = None,
    compact: bool = False,
) -> dict[str, str | None]:
    """Prefer the peer-declared profile name over the local word-pair alias.

    Precedence follows SimpleX's own profile model: ``displayName`` is the
    shown name and ``fullName`` is supplementary, so the projection uses the
    profile display name first, then the full name, then the local display
    name, and finally the scoped transport identity. Only supplied names are
    used; blank values fall through.
    """
    native = native_namespace(data)
    sender = source_transport_id
    label = (
        _supplied(native.get("sender_display_name"))
        or _supplied(native.get("sender_full_name"))
        or _supplied(native.get("sender_label"))
        or sender
    )
    if compact and isinstance(label, str):
        label = label.replace(" ", "")
    return {
        "source_sender_id": sender,
        "source_sender_label": label,
        "source_sender_short_label": label,
        "source_sender_handle": sender,
    }


def _supplied(value: object) -> str | None:
    """Accept one non-blank string name; blank or non-text values fall through."""
    if isinstance(value, str) and value.strip():
        return value
    return None
