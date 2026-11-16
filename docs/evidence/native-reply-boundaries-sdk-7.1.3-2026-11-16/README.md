# Native reply boundary follow-up

This supplements the [six-case peer campaign](../native-quotes-sdk-7.1.3-2026-11-15/README.md)
at the same immutable source pair and official SDK 7.1.3. The [manifest](manifest.json)
identifies capture/harness hashes, source equivalence, scope and teardown.
The raw [sender trace](capture/sender.jsonl) and independent native
[recipient trace](capture/recipient.jsonl) preserve UTC and host-monotonic times.
The separate identities use separate native profiles and processes on one host.

## Observed results

- Restart: PID 201842 submitted one reply and exited; PID 201913 redrove the same
  handoff, reused its accepted native reference and submitted zero sends for it.
  The recipient retained exactly one native quote.
- Uncertain submission: PID 202374 was killed after send bytes drained. Fresh
  PIDs 202396 and 202414 returned the expected permanent fence rejection, with
  zero retry sends and an unchanged unresolved reservation. This run retained
  zero sender/recipient copies of the uncertain message.

[Results](capture/results.json) distinguish handoff sends from separate
connectivity barriers. After each barrier was independently received, the
recipient observed a ten-second window. Heartbeat/history snapshots span both
windows. The verdict requires the exact fence error, zero retry sends, a retained
reservation and complete teardown. An unrelated exception cannot qualify it.
Ambiguity may legitimately produce zero or one native delivery; neither a
permanent nondelivery claim nor permission to resend follows from zero copies.

The first four cases remain supported by the original captures. Static comparison
verifies that the merged CLI additions did not alter native handoff behavior.
Core source bytes are unchanged between the tested and promotion baselines.
Native replies retain scope verification and readable target fallback; threads
remain fallback. No retry or delivery-confirmation semantics change.

Offline tests verify record consistency and causal ordering, not observation
authenticity. The sender trace accounts for the four recorded native sends
(two handoffs and two barriers) within their respective worker lifetimes. For
observed deliveries, captured text hashes match the recorded send-body hashes.
The tests bind the summary to the terminal case record and the recipient
observer's identity, SDK and lifecycle. They do not independently verify the
unretained sender-history snapshots or profile-lease reacquisition. The callback storage sinks are not a full routed core-storage
proof. Other SDKs, platforms, official deployment and sustained load are outside
this native campaign. Original evidence remains unchanged.

## Ordinary core planning probe

The [planned reply](capture/planned-reply.json) was separately executed against
provider commit `72328a92ea21229222ec04bd34745a19ac653c9a` and the same tested core.
The real RuntimeBuilder used its normal capability-driven routing, relation
binding, rendering and durable receipt/storage path. No delivery strategy was
forced: the receipt reports `direct` and `sent`, and the independent recipient
received one scoped quote to its local parent item 28. The [recipient stream](capture/planned-recipient.jsonl)
retains that observation and observer lifecycle. The offline checks require the
live recipient event and matching history, verify the intended parent text, and
reject any different recipient item ID carrying the nonce across the stream.
The probe does not retain a sender trace or an independently measured observation
window; the durable receipt and clean runtime stop are recorded in its summary.
This one routed probe supplements
the handoff-boundary cases; their callback-sink scope remains distinct.

## Harness and clock interpretation

Each reservation check rereads persisted state with
`SELECT request,native_id FROM handoffs WHERE outbox=? AND attempt=?`, using the
same handoff provenance key before and after each retry. A missing row or a
changed request/native result fails the comparison. The recorded digest omits
those query parameters, but it is not a substitute for the keyed database read.
This verifies logical reservation state, not SQLite physical row identity.

The retained harness checks the exact public fence error message as well as the
exact `AdapterPermanentError` type, zero sends and the unresolved reservation.
The trace stores the exception class rather than repeating its message; an
unrelated error does not satisfy the live harness. Offline checks additionally
bind each retry to its fresh post-crash worker and each observation window to
its completed redrive and independently received case-specific barrier. They
retain distinct observed message IDs across snapshots/events, so a disappearing
duplicate cannot be hidden by the last snapshot. The intended parent text comes
from the earlier sender capture, and the recipient-local parent ID remains 28.

Sender and child recipient observer ran on the same Linux host and both used
`time.monotonic_ns()`. Cross-stream comparisons therefore share a clock domain;
they are not a conversion between independent host clocks. The captured UTC
values provide human-readable timestamps alongside that monotonic ordering.
The normal-routing probe used the same adapter source bytes as this promotion;
subsequent evidence-only revisions do not change those runtime bytes. These are
reviewed facts about the retained harness and source comparisons, not a claim
that digest checks authenticate arbitrary future captures or qualify code changes.
