# Native quote campaign: capture index and limits

This directory retains the original observations and captures from the
November 15 campaign (November 16 UTC). The recorded core and provider
commits are `989f44336e1f009ca89d7b50c8bbe0794ca251a6` and
`a049e2f3264c76d57b2105b796ceda25b1bc6c90`. The captures preserve profile-local
IDs and synthetic nonce-bearing messages from the dedicated test profiles;
these are not production-message retention examples. Display names are not
verified identities. Keep the recorded campaign distinct from later source
revisions, SDK releases and operator-generated capability claims.

| Case | Retained evidence | Limits |
| --- | --- | --- |
| Direct quote | [Recipient](evidence/case1-direct-quote-recipient.json), [sender](evidence/case1-direct-quote-sender.json) | Quote resolves to the corresponding local parent ID on each profile. |
| Group quote | [Recipient](evidence/case2-group-quote-recipient.json), [sender](evidence/case2-group-quote-sender.json) | Same relationship in an established group. |
| Attribution | [Summary](evidence/case3-origin-attribution.json) and the above recipient captures | Rendered source label, not identity authentication. |
| Deleted-parent fallback | [Post-deletion sender](evidence/case4-parent-deleted-sender.json), [fallback sender](evidence/case4-parent-fallback-sender.json), [recipient](evidence/case4-parent-fallback-recipient.json) | Parent absent from the retained sender snapshot; recipient sees readable fallback without a native quote. |
| Restart | [Sender](evidence/case5-restart-no-duplicate-sender.json), [recipient](evidence/case5-restart-no-duplicate-recipient.json) | One matching boundary message in each retained history. The restart and resubmission sequence is reported by the campaign, not independently timestamped in these captures. |
| Ambiguous submission | [Summary](evidence/case6-ambiguous-fenced.json), [sender history](evidence/case6-fenced-sender-history.json) | Reports fenced redrives and zero recipient matches. No separate raw recipient stream or observation interval is retained; this is bounded campaign evidence, not a proof of permanent nondelivery. |

The [observations record](observations.json) satisfies the structural validator
at its selected revision pair. Regression tests check the cross-references and
consistency of the captured histories. Neither establishes observation
authenticity, verifies installed SDKs on the hosts, nor reconstructs missing
campaign logs. For capability promotion, review the original procedure and
retain independent recipient observation intervals and restart/fault-injection
traces (or repeat those cases). Replies remain advertised as fallback. Do not
promote a capability or compatibility pin on the basis of this archive alone.
