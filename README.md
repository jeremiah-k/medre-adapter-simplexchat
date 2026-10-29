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

For a persistent MEDRE tool installation, `medre adapters install simplexchat`
adds the provider to the same environment. Use `medre adapter simplexchat` for
profile commands. The standalone `medre-simplexchat` entry point remains
available in activated project environments with the same arguments.

## Create a dedicated profile

```bash
uv run --locked medre adapter simplexchat init --adapter sx --name 'MEDRE bridge'
uv run --locked medre adapter simplexchat join --adapter sx
uv run --locked medre adapter simplexchat groups --adapter sx --json
```

`join` prompts for a private contact or group connection link without echoing it.
Use `--link-stdin` for automation. A requested connection is persisted, and the
command keeps native networking active for up to `--wait-seconds` (60 by default).
A connection observation does not establish group membership. Inspect `groups`;
accept an invited group with `accept-group --group-id ID`. Use `connect` to resume
pending connections for a bounded interval. The remote peer may need to approve.

`create-group --name 'Bridge room'` creates a bot-owned group. `contacts` lists
direct chat IDs. Group/member/contact IDs are local to this dedicated profile;
they are not global SimpleX identities.

Profiles default to MEDRE's private instance state directory under
`adapters/sx/simplexchat/profile`; `MEDRE_HOME` and XDG paths are honored. Use the
same `--adapter` and optional `--database-prefix` for setup and runtime. When a
runtime YAML exists, `--config bridge.yaml --adapter sx` selects its profile and
honors MEDRE's environment overrides. Initialization is explicit; starting MEDRE
does not silently create a SimpleX identity.

Stop MEDRE before profile commands. MEDRE commands exclusively lock the profile;
unrelated SimpleX clients do not honor this lock and must not open it concurrently.
The profile is dedicated to one MEDRE adapter and one active native user.

## Configure conversations

Use IDs from this profile's `groups` or `contacts` output:

```yaml
adapters:
  simplexchat:
    sx:
      user_id: 1
      chats: [group:7, contact:3]
      origin_label: SimpleX Chat
```

Routes use explicit channel strings such as `source_channel: "group:7"` and
`dest_channel: "group:7"`. Both source and destination channels must be allowlisted.
Ordinary active groups and accepted active contacts are supported; private group
support conversations are excluded. Run `medre config check` before starting.

Set an encrypted profile's key with `MEDRE_ADAPTER__SX__DATABASE_KEY`; keep it out
of YAML and arguments. `database_prefix` and `state_dir` override private storage.
On Linux, `native_cache_dir` controls the SDK cache through XDG; on other systems
the SDK uses its platform cache. `libs_dir` selects an existing official native
library directory on any supported platform.

## Delivery and recovery

Text is rendered with MEDRE's relay attribution. Reply/thread fallbacks are plain
text; native quotes, attachments, edits, deletions, reactions, typing, and puppeting
are not advertised. Received control items, sent echoes, blocked members, file
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

## Develop and validate

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
this adapter against core `main`, recording both revisions. Its manual `core_ref`
input qualifies a candidate core revision without committing temporary branch pins.

Run tests from a development environment containing core and this provider:

```bash
python -m pytest -q
python -m simplex_chat install
python -m pytest -q -m simplexchat_sdk
```

The default suite uses deterministic SDK boundaries and real subprocess ownership
tests. The opt-in SDK tier creates a disposable native profile and self-owned group
and proves local queue acceptance. It does not prove remote message receipt.
Independent-profile group routing and adding SimpleX to the native all-adapter
campaign require a separate live qualification run.

The [provider contract](docs/spec/simplexchat.md) and
[configuration schema](docs/schemas/config.schema.json) describe the runtime
boundaries and versioned metadata projection.
