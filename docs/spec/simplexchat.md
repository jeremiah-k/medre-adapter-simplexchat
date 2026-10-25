# SimpleX provider contract

## Native ownership

The provider uses the released official Python SDK inside one owned worker
process per dedicated SQLite profile. It MUST refuse simultaneous MEDRE ownership
of that profile. A profile MUST exist before runtime startup; only the explicit
profile CLI creates an identity. The configured active user MUST match the native
active user. Runtime discovery and SDK-free fakes MUST NOT initialize libsimplex.

Shutdown bounds the close request, stdin drain, process reap, and stdout drain.
Native descendants that inherit stdout MUST NOT keep the adapter alive after its
owner exits. Timeout and cancellation retain uncertain outbound claims.

## Conversations and ingress

Allowlisted channels use group:ID or contact:ID. IDs are profile-local positive
int64 values. Active ordinary groups and active accepted contacts are eligible;
private member-support conversations are excluded. Native references and sender
identities MUST include the profile and user scope.

Only complete unedited received text is canonical message history. Native sent
items, files, controls, blocked or locally hidden group members, and partial live
messages MUST NOT enter the canonical stream. Metadata exports only the versioned
public projection described by the native namespace schema.

First use baselines retained history. Restart recovery walks every native page,
retains page anchors rather than offline payload history, and re-reads missing
pages oldest first. Group history order MUST NOT be treated as increasing native
item-ID order. Core admission precedes processed-ID persistence; failure leaves
the item retryable with the same canonical UUID. Recovered ID writes are batched
per page. Both native state and the processed-ID ledger are required for recovery.

Live buffering is bounded to 256 records and 1 MiB of serialized payload. Overflow
MUST stop the native stream for recovery rather than overwrite evidence. Native
history that expires or is deleted before recovery cannot be reconstructed.

## Outbound handoff

Rendering uses plain text with relay attribution and explicit reply/thread
fallbacks. The adapter MUST validate channel, payload type, text budget, and exact
attempt provenance before sending. A reservation is durable before native send.
Only a refusal proven to precede command submission may release that reservation.

An accepted native item MUST match the selected conversation, sent direction,
and exact text. Its local queue reference is not evidence of remote receipt.
Exact confirmed attempts are idempotent. Uncertain outcomes or conflicting
provenance MUST NOT cause automatic resubmission.

The exact-text check refers to the bytes reserved before the first native send.
A renderer upgrade MUST NOT rewrite an accepted reservation or trigger another
native submission for the same provenance and conversation. Returning that
retained native ID MUST identify the result as reuse of an earlier handoff; it
MUST NOT claim that newly rendered text was submitted. An unconfirmed reservation
remains bound to its original bytes and MUST NOT authorize automatic resending.

## Configuration and capabilities

The configuration block schema describes fields nested below the adapter ID;
adapter_id is supplied by the outer mapping. Runtime validation additionally
checks int64 conversation IDs and finite timeout values. CLI environment overrides
select the same profile as the bridge. Discovery output escapes terminal controls
and keeps unknown native exception text private.

Text, direct/group conversations, native store-and-forward, and fallback
replies/threads are advertised. Attachments, edits, reactions, deletions, typing,
read receipts, presence and puppeting are not advertised. The bridge does not
extend a native platform's encryption guarantee across other transports.

Peer display names used for relay attribution must be printable on one line;
invalid labels fall back to the scoped sender identity. Shutdown releases profile
ownership even when process reaping exceeds its deadline. Restarting with the
same handoff ledger preserves accepted native message IDs without resubmission.
