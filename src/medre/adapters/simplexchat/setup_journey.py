"""Typed informational provisioning journey for the form-less SimpleX provider.

SimpleX profile, contact, and group provisioning cannot happen offline, so
the provider declares no ``native_setup_fields`` form and provisioning stays
operator-owned. Instead it declares its README journey as typed setup
instructions: ordered, verbatim provider commands with evidence notes at
their true levels. The declaration is informational only — core renders it
and never executes it because the commands contact the native service. A
completed step is never relay acceptance, delivery, or read evidence and
grants no send authority; a linked contact or a created group is not an
independently witnessed delivery.

Cores without the provider-setup-instructions seam provide no such symbols;
``journey`` then returns ``None`` and the spec constructs exactly as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from medre.adapter_spec import NativeSetupInstructions

TITLE = "SimpleX Chat profile provisioning"

SUMMARY = (
    "Provision a dedicated SimpleX profile with the provider's own commands "
    "before configuring MEDRE, then verify results at their true evidence "
    "levels. Stop MEDRE first: profile commands exclusively lock the profile "
    "and must not race a running relay. Pick one --adapter ID and reuse the "
    "same selection for setup and runtime. Every command contacts the native "
    "service; core renders this journey for information only and never "
    "invokes it, writes no configuration, and claims no provisioning."
)

# Ordered verbatim commands exactly as the README journey and the shipped
# cli_contrib parser accept them: no secrets, no building-machine paths, and
# no timestamps. Group and contact IDs are local to the provisioned profile;
# the accept-group placeholder names the local ID reported by groups.
STEPS: tuple[tuple[str, str, str], ...] = (
    (
        "medre adapter simplexchat init --adapter sx --name 'MEDRE bridge'",
        "Create the dedicated private bot profile and its active native "
        "user; run it only while MEDRE is stopped.",
        "Proves a local native profile exists with the declared bot display "
        "name. It proves nothing about connectivity, peers, or messages: not "
        "relay acceptance, delivery, or read evidence, and grants no send "
        "authority.",
    ),
    (
        "medre adapter simplexchat join --adapter sx",
        "Connect a private contact or group invitation link. The link is "
        "read from a hidden prompt without echoing it, or from stdin with "
        "--link-stdin; native networking stays active for up to "
        "--wait-seconds (60 by default) and the remote peer may need to "
        "approve.",
        "Proves a connection request was persisted and, when the command "
        "reports an observed connection, that this profile's native session "
        "observed it. Observing a connection does not establish group "
        "membership, and a linked contact is not an independently witnessed "
        "delivery: not relay acceptance, delivery, or read evidence, and "
        "grants no send authority.",
    ),
    (
        "medre adapter simplexchat groups --adapter sx --json",
        "List this profile's groups with their local IDs and membership "
        "status to select routing channels.",
        "Proves the local listing of this profile only: group and member IDs "
        "are local to this dedicated profile. A connected contact does not "
        "prove group membership; accept an invited group with accept-group "
        "before routing it. Not relay acceptance, delivery, or read "
        "evidence, and grants no send authority.",
    ),
    (
        "medre adapter simplexchat accept-group --adapter sx --group-id ID",
        "Accept an invited group by its local group ID from the groups "
        "listing before routing it.",
        "Proves this profile recorded the accepted invitation in its local "
        "membership state. It does not prove any peer witnessed the "
        "membership or received a message: not relay acceptance, delivery, "
        "or read evidence, and grants no send authority.",
    ),
    (
        "medre adapter simplexchat connect --adapter sx",
        "Resume pending native connections for a bounded interval when a "
        "join or accept-group request has not been observed yet.",
        "Proves the profile attempted to complete its pending connections "
        "within the interval. A request that stays pending proves nothing "
        "beyond the persisted request, and observing a connection does not "
        "establish group membership: not relay acceptance, delivery, or read "
        "evidence, and grants no send authority.",
    ),
    (
        "medre adapter simplexchat create-group --adapter sx --name " "'Bridge room'",
        "Create a bot-owned group with the declared name for routes that "
        "originate from this profile.",
        "Proves the group exists in this profile with the bot as owner. "
        "Other members join only through the provider's own clients by "
        "invitation or link, so peer membership is not witnessed here: not "
        "relay acceptance, delivery, or read evidence, and grants no send "
        "authority.",
    ),
    (
        "medre adapter simplexchat contacts --adapter sx",
        "List this profile's direct conversation IDs to select contact "
        "channels for configuration.",
        "Proves this profile's local contact listing. Contact IDs are local "
        "to this dedicated profile, and a linked contact is not an "
        "independently witnessed delivery: not relay acceptance, delivery, "
        "or read evidence, and grants no send authority.",
    ),
)


def journey() -> NativeSetupInstructions | None:
    """Build the typed journey when core provides the seam; otherwise None."""
    try:
        from medre.adapter_spec import NativeSetupInstructions, NativeSetupStep
    except ImportError:  # Core without the provider-setup-instructions seam.
        return None
    return NativeSetupInstructions(
        title=TITLE,
        summary=SUMMARY,
        steps=tuple(
            NativeSetupStep(command=command, purpose=purpose, evidence_note=note)
            for command, purpose, note in STEPS
        ),
    )
