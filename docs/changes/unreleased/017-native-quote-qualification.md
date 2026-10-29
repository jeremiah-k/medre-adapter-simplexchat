# Qualify the pinned SimpleX native-quote API

The pinned `simplex-chat==7.0.2` client supports `quotedItemId` in
`api_send_text_message` and `api_send_messages`. SDK-gated API contract tests
establish that exact shape through both the convenience API and native command
serialization for direct and group chats, without starting a native controller
or fabricating recipient acknowledgement. The adapter continues advertising
fallback replies: promotion requires an immutable profile/channel-scoped
reference in its durable handoff ledger, a native conversation ownership check,
a coherent restart fence, and an independent native-peer qualification.
