# Two-profile local recipient run, 2026-11-23

One executed operator run of the [two-profile local recipient tier](../../qualification/two-profile-recipient.md)
at the immutable source pair [recorded in the verdict](verdict.json):
core `267336775390fabd20537672ed5af4ea07b82e40` and provider
`efc4786c42d350bf691d8037ddfc48c8bb50e1ad`, with the official client SDK at
the manifest pin. The campaign environment's Python was 3.14. The production
adapter source was verified byte-identical to the pinned provider revision
before the run; the harness commit recorded in the verdict is the same
revision because the run executed from that exact provider source tree.

## Run mode

- One `pytest -m live` execution of `tests/test_two_profile_recipient.py`
  inside the prepared campaign environment: [JUnit result](junit.xml) reports
  one test, zero failures, zero errors, no skips, about thirty seconds wall.
- Two dedicated native profiles, independently controlled processes, on one
  Linux host; an unprivileged local messaging-server container (non-root
  user, tmpfs-only storage, no host binds, published to host loopback only)
  was the only enabled server for both profiles; the
  [server recipe](../../qualification/two-profile-recipient.md#local-messaging-server)
  reproduces it.
- The verdict record is content-free: profile tags, digests, opaque native
  references, monotonic timestamps and process identities only. Raw traces,
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

The offline verdict refuses incomplete or self-contradictory records for the
selected revisions; it cannot authenticate that recorded observations are
true. The retained private traces are the audit trail for this execution.
One run at one moment does not qualify other environments, sustained load,
or a changed source pair. No receipt, retry, or delivery-confirmation
semantics changed; local-queue acceptance remains distinct from recipient
observation, and the recipient observation recorded here is transport-level
ingress by the second profile, not a new product promise.
