# SimpleX native quote ownership (candidate)

- Bind core-scoped native reply targets to the active SimpleX profile, user and
  exact conversation, with readable fallback on pre-reservation mismatch.
- Seal quote identity and fallback bytes with the existing SQLite handoff
  request; preserve legacy text reservations and uncertain-send fences.
- Serialize the pinned SDK's `quotedItemId` without changing local queue
  confirmation to a remote delivery claim.
- Keep advertised reply capability at `fallback` until independently observed
  group/direct peer qualification and restart campaigns pass.

Verify retained parents through native item inspection after sealing the quote
and fallback. Reuse accepted handoffs without consulting an expired parent;
keep uncertain submissions fenced and native capability withheld.

Keep generated SDK message types behind type-checking guards so native worker
bootstrap also works with the SDK-free subprocess boundary stub.
