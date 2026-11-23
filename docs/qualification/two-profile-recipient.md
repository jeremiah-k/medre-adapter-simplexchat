# Two-profile local recipient qualification

Native reply SDK shape, recorded handoff traces and SDK-free restart or
ledger tests are not evidence that an independently controlled recipient
observed a delivery. This tier adds that evidence for the current pinned
source pair: two dedicated native profiles on one host, restricted to one
operator-provided local messaging server, driving the adapter's production
delivery and ingress paths.

## Required cases

1. **Routed group send observed**: a handoff through `SimplexChatAdapter`
   delivery reaches the second profile, which observes the exact plaintext
   on the target group and attributes it to a member captured before any
   observation existed.
2. **Native quote target**: a reply quoting the accepted parent is observed
   by the recipient quoting its own retained parent item with the parent's
   text digest; a missing or wrong quote target cannot qualify.
3. **Restart with continuation**: the owned sender worker process group is
   killed; a fresh worker on the same profile redrives the accepted handoff,
   reuses the identical native reference and submits zero sends; the
   recipient retains exactly one copy.
4. **Admission across a transient worker failure**: while the sender worker
   is down, the recipient sends a group message; after restart the
   production ingress admits it durably through the journaling context,
   whether by recovered history pagination or by live native events.

## Required refusals

A positive verdict is impossible when the recipient was not observing, when
a sibling destination received the message, when the ledger identity
disagrees with the recorded send, when an observation attributes a peer
outside the provisioning-time roster, when one native item is observed
twice, or when the record binds another core, provider or SDK revision.
Revisions are selected explicitly (`--core-commit`, `--provider-commit`);
the harness refuses to run against a different installed core tree, a
provider checkout at another revision, or a provider whose production
source differs from the pinned revision. No "recipient" claim may be based
on a local SDK queue return: every claim is an observation record produced
by the second profile's own worker process.

## Environment and procedure

Use a prepared campaign environment per the hardware lab service
qualification procedure: exact core and provider checkouts, the locked core
environment with an editable provider install, and the pinned official
client SDK verified with `--check-sdk` before any run. Provisioning
(profile creation, local-server restriction, group creation, full-link
invitation and acceptance) is an explicit campaign step performed through
the SDK before the production worker processes take over. Provisioning must
use the server-embedded full connection link; hosted short links are
refused so the network stays on the selected local server. Both profiles
must end up with exactly one enabled server, the local one.

```shell
python scripts/qualify_two_profile_recipient.py --check-sdk
MEDRE_SIMPLEX_TIER_SERVER="smp://..." \
MEDRE_SIMPLEX_TIER_CORE_COMMIT="$CORE_COMMIT" \
MEDRE_SIMPLEX_TIER_PROVIDER_COMMIT="$PROVIDER_COMMIT" \
MEDRE_SIMPLEX_TIER_ROOT=/private/campaign-root \
  python -m pytest tests/test_two_profile_recipient.py -m live \
  --junitxml=/private/campaign-root/junit.xml
```

The tier fails rather than skips when started without a complete campaign
environment. Raw traces, links, server addresses and profile databases stay
private; the [recorded verdict](../evidence/two-profile-recipient-2026-11-23/README.md)
is content-free.

## Offline verdict gate

`scripts/qualify_two_profile_recipient.py --validate` refuses incomplete,
self-contradictory or foreign-revision records in the default test tier;
the offline gate cannot authenticate observations, only reject records that
disagree with themselves or with the selected revisions. Filling the
template with `true` is not qualification. This gate is separate from the
older native-quote peer evidence gate; each evidence format keeps its own
record shape and validation.

## Boundaries

The tier covers one execution at the selected revisions on one host. It
does not change receipt, retry or delivery-confirmation semantics; the
adapter's `local_queue` confirmation level remains a native local-queue
fact, and an independent recipient observation recorded here is transport
evidence, not a new product promise or delivery confirmation. Sustained
load, additional peers, hosted relays and later source pairs stay
unqualified until their own campaign and promotion review.
