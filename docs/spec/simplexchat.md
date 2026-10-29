# SimpleX provider contract

## Native ownership

The provider uses the released official Python SDK inside one owned worker
process per dedicated SQLite profile. It MUST refuse simultaneous MEDRE ownership
of that profile. A profile MUST exist before runtime startup; only the explicit
profile CLI creates an identity. The configured active user MUST match the native
active user. Runtime discovery and SDK-free fakes MUST NOT initialize libsimplex.

Shutdown bounds the close request, stdin drain, process reap, and stdout drain.
POSIX native descendants that remain in the worker's process group MUST keep
profile ownership fenced after its leader exits. A reaped leader's numeric PID
MUST NOT be signalled again: that PID may have been reused by an unrelated
group. Failed descendant cleanup is reported for an explicit retry instead
of releasing the lease. Startup and shutdown MUST serialize the subprocess
creation boundary; a cancelled spawn cannot leave a worker without a lease.
Timeout and cancellation retain uncertain outbound claims.

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
ownership only after its owned process is confirmed reaped. Failed or cancelled
cleanup MUST retain the lease and session reference, refuse further native calls,
and permit a serialized cleanup retry. A cancelled adapter task gather MUST
mark health failed before admitting further calls. Concurrent stops MUST share
one cleanup owner; a completed stop MUST NOT signal a stale process identifier.
Startup and shutdown MUST serialize ownership changes, including repeated
cancellation while subprocess creation is pending. On POSIX, an exited leader
MUST NOT be signalled through its retained process-group ID; a surviving group
MUST keep the profile fenced. Adapter health MUST report failed when native
shutdown remains incomplete. Restarting with the same handoff ledger preserves
accepted native message IDs without resubmission.

Failed CLI cleanup retains a strong session reference while the Python owner
remains alive. On non-Linux systems, process termination can still release the
profile lease before a surviving native descendant exits. Linux additionally
uses the inherited file-description guard described below. The guard protects
only workers that retain its descriptor; independent descendants that close it
still require separate qualification.

## Linux owner death and stopped-state inventory

On Linux the profile owner also holds a `flock` guard file at
`database_prefix + ".medre-owner.lock"`, and passes the guarded file descriptor
into the native worker. Release MUST close the owner's descriptor without
`LOCK_UN`; explicitly unlocking a shared file description would also revoke the
worker's fencing. The kernel retains the lock until the last holder closes it.
A surviving native worker therefore retains the file
lock even if the Python bridge terminates abruptly and SQLite's owner
transaction is released. A new MEDRE owner checks the same guard before
opening the native profile. Do not unlink or replace the lock file while
any native owner may be running. This protects the native worker process; an
SDK that independently spawns descendants and closes the inherited descriptor
still requires separate process-tree qualification. Other operating systems
retain the existing SQLite lease semantics without this Linux guarantee.

A stopped-state inventory must include both adapter-owned `handoffs.sqlite`
and `inbox.sqlite` in `state_dir`, the owner database at
`database_prefix + ".medre-owner.sqlite"`, the Linux guard file when present,
and **all** official SDK profile databases and identity-bearing files derived
from the installed SDK's `database_prefix`. The configured
`native_cache_dir` is the worker's `XDG_CACHE_HOME`; determine whether it
contains persistent identity material for that SDK version before omitting it.
Do not infer native database filenames or claim a coherent restore from only
the MEDRE ledgers. Stop the bridge and confirm its native process tree exited
before capture or restore; saved lock-file bytes do not transfer a live lock.

## Native health notifications

The provider MUST use the same retained native state for health probes and the
optional `AdapterContext.report_health` sink. Reports MUST describe settled
`healthy`, `degraded` or `failed` observations for the bound adapter. Startup and
stop MUST delimit the reporting lifetime; retired callbacks MUST NOT publish
for a replacement. Observer exceptions MUST NOT interrupt native operations,
release ownership, create receipts or authorize another send. Contexts without
the sink retain explicit-probe compatibility.

Successful local profile/event access establishes healthy native-owner state.
Admission failure MUST report degradation while retaining its existing retry
and native evidence. Native worker/profile failure MUST report failed health
without claiming profile ownership was released. Contact and group recipient
reachability MUST NOT be inferred from local profile liveness.

Startup and shutdown errors MUST propagate to the runtime lifecycle owner.
Native reporting MUST remain fenced during failed shutdown, and retained failed
health MUST remain available to explicit inspection. Reporting a lifecycle error
MUST NOT reopen a retired native callback lifetime or admit further sends.

## Stopped-state declarations

The plaintext-profile declaration MUST select both native databases, the
namespace database, handoff and inbox ledgers, and the profile guard inode.
Retained lock bytes MUST NOT be treated as live exclusion after restore.
An encrypted profile MUST refuse automatic planning until a coherent provider
export can preserve both its native identity and adapter journals. Reporting
zero files as a successful complete profile capture is not supported.

## Operator command namespace

The provider MUST contribute its profile commands under `medre adapter simplexchat`
through MEDRE's isolated CLI registration and dispatch hooks. Generic help MUST
remain SDK-free and MUST NOT open a native profile. The standalone
`medre-simplexchat` entry point MUST use the same parser definitions and execution
policy. Both paths MUST retain exit status, hidden secret input, profile locks,
native cleanup, and configuration/environment selection. Provisioning remains
an explicit operator action; runtime startup MUST NOT create an identity.

## Common diagnostic projection

Native and SDK-free adapters MUST expose the core common diagnostic keys.
`health` MUST match the same retained observation returned by `health_check`;
`mode` MUST distinguish `live` from `fake`. Legacy provider-specific keys MUST
remain available. Unobserved connection state, uncounted delivery failures,
and unavailable reconnect/error details MUST remain `None`, rather than
fabricated success, zero counts or inferred remote reachability. Cumulative
`reconnects` MUST NOT be relabeled as consecutive `reconnect_attempts`.

## Native quote qualification boundary

The pinned official SDK exposes `api_send_text_message(..., in_reply_to=ID)`
which serializes `quotedItemId` into a composed message. Contract tests also
exercise `api_send_messages` through native command serialization for direct
and group chat references. This is an SDK shape fact, not permission to use an untrusted canonical ID as a native item ID.
MEDRE's current worker sends plain text without a quote carrier; its handoff
ledger binds only attempt, channel and body. Reply capability MUST remain
`fallback` until an implementation binds a profile- and channel-scoped native
reference into that durable reservation and validates the quote against the
selected conversation immediately before native submission. Stale, absent,
cross-chat, or cross-profile targets MUST retain readable fallback, never
switch to an unrelated local item. A crash after invoking the native API
MUST NOT cause a second send without independent proof of non-acceptance.
