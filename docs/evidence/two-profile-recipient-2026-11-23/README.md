# Two-profile local recipient run, 2026-11-23

This directory records two executions of the
[two-profile local recipient tier](../../qualification/two-profile-recipient.md):
the historical **version 1** campaign at the original operator-selected
production source pair, and the **version 2** campaign that qualified the
strengthened gate later the same day.

## Version 2 campaign

Executed 2026-11-23 in a freshly prepared campaign environment on the same
host, at core `267336775390fabd20537672ed5af4ea07b82e40` and provider
`6d5b7eba531e6b9bab8d95ba686903ae6c2f8d79`, with the official client SDK at
the manifest pin. The provider and the executed harness revisions are
identical: the harness verified at run time that its own bytes are committed
at that revision and that the imported core and provider production sources
are clean at their pins. The campaign environment's Python was 3.14.

- Messaging server: disposable `simplexchat/smp-server` container from the
  immutable image digest
  `sha256:7d825822839a9d5ee9e9a99563d77a319816c7a8e0b4b7bf4dcebe448897af54`,
  run unprivileged per the
  [server recipe](../../qualification/two-profile-recipient.md#local-messaging-server)
  (non-root user, memory-backed state, no host binds, one loopback-published
  port) and torn down after the campaign; anonymous Docker volume and
  container counts matched their baseline exactly afterwards.
- Run mode: one `pytest -m live` execution of
  `tests/test_two_profile_recipient.py`, one test, zero failures, zero
  errors, no skips, about thirty seconds wall. The generated JUnit XML and
  the verdict record stay in the operator's private campaign directory and
  are not part of the repository. The offline gate revalidated the produced
  version 2 record standalone with `--validate` at the same revision pair.
- `group_send_observed`: the routed handoff through the production delivery
  path reached the independent recipient as exactly one observation on the
  target group, zero sibling deliveries, plaintext digest equal to the
  recorded send, attributed to a provisioning-time roster member, with an
  accepted ledger whose identity matches the independently constructed
  attempt.
- `native_quote_target`: the recipient observed the reply quoting its own
  retained parent item by item id and by parent text digest; the reply
  observation follows the parent observation and the send.
- `restart_continuation`: the sender worker process group was reaped with a
  confirmed `SIGKILL` exit; a fresh worker process redrove the accepted
  reply handoff after replacement readiness, reused the identical native
  reference, submitted zero send requests counted at the worker send
  boundary, and a fresh retained-history observation after redrive found
  exactly one reply copy with matching quote identity.
- `transient_worker_admission`: the recipient sent while the sender worker
  was down, strictly between the confirmed exit and the replacement launch;
  after restart the production ingress admitted it durably as
  `message.created` through live native events (`provenance: "live"`).
- Limits: this is one execution at one moment at this source pair and image
  digest on one host. It does not qualify other environments, sustained
  load, additional peers, hosted relays, or any later source pair; no
  receipt, retry, or delivery-confirmation semantics changed, and the
  recorded observation is transport-level ingress by the second profile,
  not a delivery confirmation.

## Historical version 1 campaign

One executed operator run of the tier
at the original operator-selected production source pair:
core `267336775390fabd20537672ed5af4ea07b82e40` and provider
`efc4786c42d350bf691d8037ddfc48c8bb50e1ad`, with the official client SDK at
the manifest pin. The campaign environment's Python was 3.14. This is a
historical **version 1** campaign result, not a version 2 qualification.
The provider pin is the production-source baseline; the harness was introduced
in the PR branch and did not exist in that older commit. The earlier record
incorrectly equated the production pin with the executable harness revision;
there is no independent immutable harness identity in the retained public
summary. Its exact source revision cannot therefore be established from the
published evidence alone. The server image digest was also not captured in
the retained public record, so its exact image cannot be reproduced from this
summary. The version 2 campaign above supersedes it for qualification.

## Run mode

- One `pytest -m live` execution of `tests/test_two_profile_recipient.py`
  inside the prepared campaign environment: the operator recorded one test,
  zero failures, zero errors, no skips, and about thirty seconds wall.
  Generated JUnit XML and the offline verdict remain in the private campaign
  directory; they are not part of the repository.
- Two dedicated native profiles, independently controlled processes, on one
  Linux host; an unprivileged local messaging-server container (non-root
  user, tmpfs-only storage, no host binds, published to host loopback only)
  was the only enabled server for both profiles; the
  [server recipe](../../qualification/two-profile-recipient.md#local-messaging-server)
  reproduces it.
- The original verdict record was intended to be content-free: profile tags,
  digests, opaque native references, monotonic timestamps and process identities. Raw traces,
  links, server addresses and profile databases stayed in the operator's
  private campaign directory and are not published.

## Observed results

- `group_send_observed`: the routed handoff through the adapter's production
  delivery path was observed by the independent recipient profile as one
  message whose plaintext digest matches the recorded send, on the target
  group only, attributed to a member recorded in the provisioning-time roster.
- `native_quote_target`: the recipient observed the reply quoting its own
  retained copy of the parent item with the parent's text digest.
- `restart_continuation`: the sender worker process group was killed
  (`SIGKILL`); a fresh worker on the same profile redrove the accepted reply
  handoff, reused the identical native reference, submitted zero sends, and
  the recipient retained exactly one reply copy.
- `transient_worker_admission`: while the sender worker was down, the
  recipient sent a group message; after restart the production ingress
  admitted it durably. In this run the message arrived through live native
  events (`provenance: "live"` in the journal entry) rather than through the
  recovered history pagination; the offline verdict accepts either
  provenance and records which one occurred.

## Comparison with the earlier quote campaign

The [earlier six-case quoted-reply campaign](../native-quotes-sdk-7.1.3-2026-11-15/README.md)
and its [boundary follow-up](../native-reply-boundaries-sdk-7.1.3-2026-11-16/README.md)
recorded independent recipient observations at an older source pair. This
run re-executed an equivalent two-profile observation at the current pinned
pair with an automated harness and an offline refusal gate; it does not
reuse, supersede, or re-scope those records, and it does not qualify any
later source pair. Capability claims remain bound to their own records.

## Limits

The version 1 offline verdict was not sufficient to refuse all incomplete or
self-contradictory records. The version 2 gate adds independent handoff
identities, roster and quote binding, worker lifecycle and actual send-request
proofs, and a fresh retained-history observation; that gate has not been
re-executed against this historical run. The retained private traces are the
audit trail for the original execution, not proof for an unseen rerun.
One run at one moment does not qualify other environments, sustained load,
or a changed source pair. No receipt, retry, or delivery-confirmation
semantics changed; local-queue acceptance remains distinct from recipient
observation, and the recipient observation recorded here is transport-level
ingress by the second profile, not a new product promise.
