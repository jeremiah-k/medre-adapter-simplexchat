# Wire the SDK-supplied native metadata into the namespace and attribution

The official Python client supplies per-item and per-peer metadata the codec
previously dropped: group-member and contact profiles, the shared stable
member ID, per-item signature verification, and mention/link flags. The
versioned native namespace now captures, when the SDK actually supplies them:
`sender_display_name` and `sender_full_name` (peer-declared profile names),
`member_id` (the protocol's shared opaque member identity that survives
`localDisplayName` changes; group conversations only), `member_role`,
`msg_verified` (`verified`/`signed_no_key`/`sig_missing`), `user_mention`,
`has_link`, and an explicit `public_facts` allowlist (`msg_verified`,
`member_role`) for the core bridge-context projection. Optional fields stay
absent when the SDK omits them; the codec never invents profile data.

`meta.msgVerified` also maps onto the canonical `transport.signature_valid`
slot: `verified` → `True`, `sigMissing` → `False`, and `signed` with
`signedNoKey` stays `None` because a signature exists but no key was available
to verify it.

Attribution precedence for relayed messages is now the peer-declared profile
`displayName`, then `fullName`, then the local word-pair alias
(`localDisplayName`), then the scoped sender identity. This follows SimpleX's
own profile model, where `displayName` is the shown name and `fullName` is
supplementary. A group message from a member whose profile says
`displayName: "Jeremiah"` now relays as `[Mesh] Jeremiah: ...` instead of
`[Mesh] Alice: ...`.

`native.schema.json` gains the optional properties. The existing key-hygiene
test keeps excluding `groupInfo.privateKey`; `memberId` is captured because
the protocol defines it as a shared member ID transmitted to all group
members, not key material. `metadata_fields` in the core capability profile
stays `false`: the contract semantic is the adapter's ability to *transmit*
structured metadata fields, and the SimpleX outbound payload remains plain
text; the namespace and public facts are inbound capture that rides other
destinations' carriers.
