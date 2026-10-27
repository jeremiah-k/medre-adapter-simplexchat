"""SimpleX chat declaration: profile databases, ledgers, and the flock guard.

Layout contract read from this distribution: the native SDK derives the agent
database ``<prefix>_agent.db`` and chat database ``<prefix>_chat.db`` from the
configured ``database_prefix``; the bridge keeps its handoff and inbox ledgers
beside them in ``state_dir`` and fences the profile with a flock guard file
whose inode is intentionally retained after release. This module mirrors that
contract without importing the transport SDK, per the seam contract in
``medre.maintenance.stopped_declare``.
"""

from __future__ import annotations

from pathlib import Path

from medre.maintenance.stopped_declare import (
    DeclaredFile,
    LocalExclusion,
    ProviderCoverage,
    StoppedStateDeclaration,
    declared,
)

_GUARD_SUFFIX = ".medre-owner.lock"
_NAMESPACE_SUFFIX = ".medre-owner.sqlite"


class SimplexChatStoppedState:
    """Provider-owned declaration of one configured native profile."""

    provider = "simplexchat"

    def coverage(self, config: object, *, adapter_id: str) -> ProviderCoverage:
        """Declare every identity-bearing file of the configured profile."""
        prefix = getattr(config, "database_prefix", "")
        state_dir = getattr(config, "state_dir", "")
        if not prefix or not state_dir:
            return declared(
                provider=self.provider,
                adapter_id=adapter_id,
                files=(),
                state_class="unsupported",
                notes=("database_prefix and state_dir must be configured",),
            )
        if getattr(config, "database_key", ""):
            # SQLCipher databases cannot pass the generic SQLite integrity
            # backup; only a provider-produced consistent export is coherent.
            return declared(
                provider=self.provider,
                adapter_id=adapter_id,
                files=(),
                state_class="unsupported",
                notes=(
                    "encrypted native profile requires a coherent provider "
                    "export with its adapter ledgers; automatic planning is refused",
                ),
            )
        files = [
            DeclaredFile(
                Path(state_dir) / "handoffs.sqlite",
                "adapter_ledger",
                "sqlite",
                purpose="queued-send dedup ledger",
            ),
            DeclaredFile(
                Path(state_dir) / "inbox.sqlite",
                "adapter_ledger",
                "sqlite",
                purpose="inbound dedup ledger",
            ),
            DeclaredFile(
                Path(prefix + _NAMESPACE_SUFFIX),
                "adapter_ledger",
                "sqlite",
                purpose="profile handoff namespace",
            ),
            DeclaredFile(
                Path(prefix + _GUARD_SUFFIX),
                "native_identity",
                "opaque",
                purpose="ownership guard inode; bytes are not a live lock",
            ),
            DeclaredFile(
                Path(prefix + "_agent.db"),
                "native_identity",
                "sqlite",
                purpose="native agent database (connections, identity)",
            ),
            DeclaredFile(
                Path(prefix + "_chat.db"),
                "native_identity",
                "sqlite",
                purpose="native chat database (profile, contacts, groups)",
            ),
        ]
        notes = [
            "copied guard bytes never establish a live lock after restore; "
            "the next owner re-fences through the same inode",
            "another host with the same native profile cannot be excluded " "locally",
        ]
        if getattr(config, "native_cache_dir", ""):
            notes.append(
                "the configured native cache directory is disposable and is "
                "never selected"
            )
        return declared(
            provider=self.provider,
            adapter_id=adapter_id,
            files=files,
            notes=notes,
        )

    def local_exclusion(self, config: object, *, adapter_id: str) -> LocalExclusion:
        """Reuse this adapter's own flock guard; never create or seize it.

        The runtime acquires this flock before every profile admission, so
        holding it retains exclusion from any local owner for the whole
        capture. flock and SQLite locks are independent families, so the held
        guard does not block SQLite backup of the captured files.
        """
        prefix = getattr(config, "database_prefix", "")
        if not prefix:
            raise ValueError("simplexchat database_prefix must be configured")
        exclusion = LocalExclusion(self.provider, note="uninitialized")
        exclusion.retain_flock(
            Path(prefix + _GUARD_SUFFIX), purpose="profile ownership guard"
        )
        exclusion.note = (
            "local flock exclusion retained for the capture duration; other "
            "hosts are not covered"
        )
        return exclusion


def declaration() -> StoppedStateDeclaration:
    """Return the provider-owned declaration for stopped-state planning."""
    return SimplexChatStoppedState()
