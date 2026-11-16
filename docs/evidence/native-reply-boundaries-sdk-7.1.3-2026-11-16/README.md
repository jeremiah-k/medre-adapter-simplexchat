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
authenticity. The callback storage sinks are not a full routed core-storage
proof. Other SDKs, platforms, official deployment and sustained load are outside
this native campaign. Original evidence remains unchanged.
