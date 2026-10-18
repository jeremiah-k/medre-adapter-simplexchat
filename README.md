# MEDRE SimpleX Chat adapter

Bridge allowlisted SimpleX Chat groups and direct conversations through the
[official Python client](https://github.com/simplex-chat/simplex-chat/tree/stable/packages/simplex-chat-python).
Group chat is the primary workflow. The provider includes plain-text attribution,
reply fallbacks, durable admission, and an SDK-free fake for development.

## Install

Keep core and adapters in one locked uv project:

```bash
uv init --bare medre-bridge
cd medre-bridge
uv add 'medre @ git+https://github.com/jeremiah-k/medre.git@main' \
  'medre-adapter-simplexchat @ git+https://github.com/jeremiah-k/medre-adapter-simplexchat.git@main'
```

Use immutable Git revisions for reproducible deployments. The provider pins the
released official `simplex-chat==7.0.2` package. Its native library is downloaded
on first initialization; no separate CLI daemon or unofficial wrapper is needed.
The SDK supports Linux, macOS and Windows on its published architectures; CI
qualifies Linux x86-64. This adapter's code is MIT licensed. The official SDK and
native library carry their own AGPL-3.0 license.

## Delivery and recovery

Text is rendered with MEDRE's relay attribution. Reply/thread fallbacks are plain
text; native quotes, attachments, edits, deletions, reactions, typing, and puppeting
are not advertised. Received control items, sent echoes, blocked members, file
captions, live partial messages, and private support chats are excluded.

The first successful scan establishes a baseline without replaying existing
history. Restart recovery walks every retained history page using native
pagination and a local ledger of processed IDs. Group history is timestamp
ordered: late messages can have higher IDs and earlier positions. Recovery does
not assume increasing IDs. Recovered events follow native page order, rather than
promising a global chronological order. Startup cost grows with retained history;
the ID ledger retains one small row per observed native item.

Live events use a native buffer bounded to 256 items and 1 MiB. Overflow stops the adapter;
restart recovery reads retained messages from the native profile. Admission
refusals retain the pending event for retry. Stable canonical IDs let core
deduplicate an admission repeated across a crash. Native history deleted or expired
before recovery cannot be reconstructed. Back up the profile, ownership sidecar,
MEDRE state, and adapter ledgers together; do not reuse these ledgers for a reset
native profile.

Outbound acceptance confirms only the native **local queue**, not remote delivery.
An exact confirmed attempt can return its stored native reference without sending
again. A conflicting attempt, uncertain native outcome, or different attempt for
the same outbox requires inspection of the native profile; the adapter refuses a
blind resubmission. Health describes the local native session, not peer reachability.
