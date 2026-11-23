# medre-adapter-simplexchat

The SimpleX Chat adapter for MEDRE.

## Install

Use [uv](https://docs.astral.sh/uv/getting-started/installation/) to install
MEDRE and this adapter in the same persistent tool environment:

```bash
uv tool install medre
medre adapters install simplexchat
```

The adapter pins the released official
[simplex-chat Python client](https://github.com/simplex-chat/simplex-chat/tree/stable/packages/simplex-chat-python)
at the manifest pin. Group chat is the primary workflow. Its native library is
downloaded on first initialization; no separate CLI daemon or unofficial
wrapper is needed. The SDK supports Linux, macOS and Windows on its published
architectures; CI qualifies Linux x86-64. The private-link export commands
require POSIX file permissions; they reject non-POSIX platforms before native
mutation. This restriction does not change the other commands. This adapter's
code is MIT licensed. The official SDK and native library carry their own
AGPL-3.0 license.

On Windows, run the printed uv installation command after MEDRE exits. Use
`medre adapter simplexchat` for profile commands. The standalone
`medre-simplexchat` entry point remains available in activated project
environments with the same arguments.

For a persistent bridge, prefer one locked uv project: add `medre` and
`medre-adapter-simplexchat` to the project and run commands with
`uv run --locked`, keeping the committed lock for reproducible deployments.
For Git development sources, select immutable revisions; existing saved Git
requests and catalogs retain their sources. See the
[adapter installation guide](https://github.com/jeremiah-k/medre/blob/main/docs/ops/adapter-installation.md).

## Provision a dedicated profile

Setup proceeds in stages: prerequisites, explicit provisioning commands, then
configuration and verification. Provisioning always contacts the
native service; initialization is explicit and starting MEDRE does not silently
create a SimpleX identity. `medre config native` renders this journey
informationally on cores with the typed setup-guidance seam; it never
provisions this transport — use the `medre adapter simplexchat` commands
below, and see
[adapter maintenance](docs/dev/adapter-maintenance.md) for the seam contract.

> The commands in this journey use `--adapter sx` as an **example** instance.
> Replace that value consistently with the actual configured adapter ID before
> running commands. `medre config native` displays instructions only; it does
> not substitute IDs, provision accounts, or create a bridge.

### Stage 1: prerequisites

- Stop MEDRE before profile commands. MEDRE commands exclusively lock the
  profile; unrelated SimpleX clients do not honor this lock and must not open
  it concurrently. The profile is dedicated to one MEDRE adapter and one
  active native user.
- Pick one `--adapter` ID (the examples use `sx`) and, when overriding storage,
  one `--database-prefix`. Use the same selection for setup and runtime.
  Profiles default to MEDRE's private instance state directory under
  `adapters/sx/simplexchat/profile`; `MEDRE_HOME` and XDG paths are honored.
- The pinned official `simplex-chat` SDK downloads its native library
  on first initialization; `libs_dir` selects an existing official native
  library directory instead.

### Stage 2: explicit provisioning commands

Run the adapter's own commands in order. In a uv deployment project, prefix
each with `uv run --locked`; the standalone `medre-simplexchat` entry point
takes the same arguments.

```bash
medre adapter simplexchat init --adapter sx --name 'MEDRE bridge'
medre adapter simplexchat join --adapter sx
medre adapter simplexchat groups --adapter sx --json
```

`init` creates the private bot profile and its active native user. `join`
prompts for a private contact or group connection link without echoing it;
use `--link-stdin` for automation. A requested connection is persisted, and
the command keeps native networking active for up to `--wait-seconds` (60 by
default). The remote peer may need to approve.

The profile can also originate connections. `create-address --adapter sx --out PATH`
creates or retains the profile's contact address and sets auto-accept using the
bot's normal profile (not a business address). This does not reject incognito
remote peers. `create-link --adapter sx --group-id ID --out PATH` creates or
exports the existing member-role invitation for a local group reported by
`groups`. Existing links with another role are refused, not changed. The
`address_created` and `group_link_created` result fields indicate whether a new
native link was created, not whether export succeeded on a prior invocation.

Both export commands require POSIX owner-only file permissions. Use an
operator-controlled output directory on a filesystem that enforces them.
The command writes a fresh `0600` file and atomically replaces the destination
entry, including a symlink entry without following its target. It does not
modify an existing inode, even if it has other hard links or open readers.
Link material is never printed. If export fails after native creation,
correct `--out` and repeat the same command against the same profile and group;
the existing link is retained. An interrupted process may leave an owner-only
temporary file in the output directory. Treat it as private too.
Peers complete the connection while `connect --adapter sx` keeps native
networking active. Group-link joins may complete without a separate approval
step. Configure and verify native admission policy separately if approval is
required; these commands do not enforce it.

Provisioning completes with explicit actions. `accept-group --adapter sx --group-id ID`
accepts an invited group, `connect --adapter sx` resumes pending native
connections for a bounded interval, and
`create-group --adapter sx --name 'Bridge room'` creates a bot-owned group.
`contacts --adapter sx` lists direct chat IDs from the same profile.

### Verification

Read each result at its true evidence level:

- `groups` and `contacts` print the profile's native listings. A connection
  observation does not establish group membership; the command output itself
  states that a connected contact does not prove group membership. Accept an
  invited group with `accept-group --adapter sx --group-id ID` before routing
  it, and use `connect --adapter sx` again when a request stays pending.
- Group/member/contact IDs are local to this dedicated profile; they are not
  global SimpleX identities.
- `create-address` and `create-link` prove local native state only. The written
  link is the capability to connect, so keep its owner-only file private.
  Inspect native membership and admission policy independently; a group-link
  join may be accepted automatically. Creating or exporting a link is not
  relay acceptance, delivery, or read evidence.
- A linked contact is not an independently witnessed delivery. Nothing above
  proves a peer received a message: outbound acceptance confirms only the
  native local queue, and health describes the local native session, not peer
  reachability. See [Delivery and recovery](#delivery-and-recovery).

## Configure

Use IDs from this profile's `groups` or `contacts` output:

```yaml
runtime:
  adapter_start_timeout_seconds: 120
adapters:
  simplexchat:
    sx:
      adapter_kind: real
      user_id: 1
      chats: [group:7, contact:3]
      origin_label: SimpleX Chat
routes: {}
```

`adapter_start_timeout_seconds` bounds the complete adapter start; the native
session needs that headroom, and large retained histories can need more.
Routes use explicit channel strings such as `source_channel: "group:7"` and
`dest_channel: "group:7"`. Both source and destination channels must be
allowlisted. Ordinary active
groups and accepted active contacts are supported; private group support
conversations are excluded. This example configures only one adapter;
add a second adapter and explicit route legs with `medre config bridge`.
Run `medre config check` and `medre routes plan` before starting; these
offline checks do not prove native acceptance or recipient delivery. For
storage, cache, and library overrides, see the
[SimpleX Chat setup guide](https://github.com/jeremiah-k/medre/blob/main/docs/ops/transport-setup/simplexchat.md).

Set an encrypted profile's key with `MEDRE_ADAPTER__SX__DATABASE_KEY`; keep it
out of YAML and arguments. `database_prefix` and `state_dir` override private
storage. On Linux, `native_cache_dir` controls the SDK cache through XDG; on
other systems the SDK uses its platform cache. `libs_dir` selects an existing
official native library directory on any supported platform. When a runtime
YAML exists, `--config bridge.yaml --adapter sx` selects its profile and
honors MEDRE's environment overrides.

## Verify

Confirm the installed adapter and the saved configuration resolve:

```bash
medre adapters check --config connections.yaml
```

The setup guide's
[Verify](https://github.com/jeremiah-k/medre/blob/main/docs/ops/transport-setup/simplexchat.md#verify)
section shows the bridge checks, recovery behavior, and evidence levels.

## Delivery and recovery

Text is rendered with MEDRE's relay attribution. When the official client
supplies a peer's profile, the peer-declared display name (then full name)
leads the attribution prefix; the local word-pair alias and the scoped sender
identity remain the fallbacks, and missing or non-printable names degrade
without invention. The codec also captures the SDK's native facts in the
versioned namespace: the shared stable member ID (an additional identifier
alongside the scoped sender identity) and role for group members,
per-item signature-verification verdicts, and mention/link flags. The explicit
`public_facts` allowlist (`msg_verified`, `member_role`) crosses into another
transport's relay context; the rest of the namespace stays out of the `off`,
`minimal`, and `safe` metadata export modes, but an operator-selected explicit
`full` metadata export embeds the whole namespace and can disclose the member
ID. Replies use destination-bound native quotes when the retained parent is
verified in the active profile and conversation. Missing, deleted or mismatched
parents retain readable fallback. Threads remain fallback; attachments, edits,
deletions, reactions, typing, and puppeting are not advertised. Received control items, sent echoes, blocked members, file
captions, live partial messages, and private support chats are excluded.

The first successful scan establishes a baseline without replaying existing
history. Restart recovery walks every retained history page using native
pagination and a local ledger of processed IDs. Group history is timestamp
ordered: late messages can have higher IDs and earlier positions. Recovery
admits missing items in oldest-page-first order without assuming increasing
IDs. Each baseline page commits its IDs in one transaction. Recovery must not
stop at a fully retained page: older pages can still contain unseen, backdated
messages. Startup cost and retained ID storage grow with native history; for
large profiles, allow additional startup time and monitor profile growth.

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
blind resubmission. If the session rejects a send _before any command is
written_, the unsent reservation is released and core can retry. A native
rejection after submission is not proof of no handoff and remains uncertain.
Health describes the local native session, not peer reachability.

## Development

Register arbitrary checkout paths with `medre dev`; sibling directories are not
required:

```bash
medre dev sync --project /path/to/medre --sources /path/to/dev-sources.yaml \
  --editable simplexchat=/path/to/medre-adapter-simplexchat simplexchat
```

The explicit source catalog can contain:

```yaml
adapters:
  simplexchat: medre-adapter-simplexchat @ git+https://github.com/jeremiah-k/medre-adapter-simplexchat.git@main
```

Add `--sdk` to install the native dependency. See `medre dev --help` for workspace
selection and dependency options. CI checks
this adapter against core `main`, resolved once per run, recording both revisions.
Keep `MEDRE_CORE_REF` unset. Its manual `core_ref`
input qualifies a candidate core revision without committing temporary branch pins.
See [contributing](CONTRIBUTING.md) for the shared Trunk quality gate and
uv-based editable development workflow; maintainer material — the
setup-guidance seam contract — lives in
[adapter maintenance](docs/dev/adapter-maintenance.md).

Run tests from a development environment containing core and this adapter:

```bash
python -m pytest -q
python -m simplex_chat install
python -m pytest -q -m "simplexchat_sdk and not local_integration and not soak and not live and not hardware and not docker"
```

The default suite uses deterministic SDK boundaries and real subprocess ownership
tests. The opt-in SDK tier creates a disposable native profile and self-owned group
and proves local queue acceptance. It does not prove remote message receipt.
Independent-profile group routing and adding SimpleX to the native all-adapter
campaign require a separate live qualification run.

The [provider contract](docs/spec/simplexchat.md) and
[configuration schema](docs/schemas/config.schema.json) describe the runtime
boundaries and versioned metadata projection.

Native reply qualification is recorded in the [boundary follow-up](docs/evidence/native-reply-boundaries-sdk-7.1.3-2026-11-16/README.md).
