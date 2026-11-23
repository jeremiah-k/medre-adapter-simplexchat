# Add the two-profile local recipient qualification tier

Add an opt-in live tier for the SimpleX adapter that observes a routed group
send, a native quote target, worker-restart continuation and admission
across a transient worker failure through two independently controlled
native profiles on one host, restricted to one operator-provided local
messaging server. The harness and its verdict gate live in
`scripts/qualify_two_profile_recipient.py`; the offline refusal logic is
covered by default-tier unit tests, and `tests/test_two_profile_recipient.py`
remains deselected unless the operator selects the `live` marker inside a
prepared campaign environment. A started run fails rather than skips without
its environment.

Positive verdicts are refused for foreign revisions, a disabled or
unready recipient, sibling delivery, ledger identity disagreement, unknown
peers, duplicate native items, and missing or wrong quote targets. The
first executed run at the pinned source pair is recorded under
`docs/evidence/two-profile-recipient-2026-11-23/` with a content-free
verdict; the qualification procedure is documented in
`docs/qualification/two-profile-recipient.md`.

No adapter or core send, receipt, retry or delivery-confirmation semantics
change; the tier adds campaign tooling, tests and evidence only. Local-queue
acceptance remains distinct from an independent recipient observation.
