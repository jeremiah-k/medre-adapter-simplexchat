# Native SimpleX quoted-reply qualification

Native quote SDK shape and SDK-free restart/ledger tests are **not** evidence of
recipient-side quote display. The provider capability remains `fallback`.

Use two distinct, independently operated native identities at exact pinned
core, SimpleX provider and official SimpleX SDK revisions. Record sanitized
observations for direct and group native quote display, readable sender/origin
attribution, deleted/expired parent fallback, restart with the same reserved
handoff, and ambiguous native submission remaining fenced (never doubled).
Keep each native recipient observation separately accessible for audit.

Run the evidence completeness gate:

```shell
uv run --no-sync python scripts/qualify_native_quotes.py --template > /private/simplex-peer-template.json
uv run --no-sync python scripts/qualify_native_quotes.py \
  --validate /private/observations.json \
  --core-commit "$CORE_COMMIT" --provider-commit "$PROVIDER_COMMIT"
```

The tool never opens profiles and cannot prove an operator's observations are
true: it only refuses incomplete or contradictory metadata. Filling the template
with `true` is **not** qualification. Verify receipts in the recipient application
and retain independently captured, redacted evidence before capability promotion.
Do not record session secrets, message bodies, profile database contents, or
private invite links. Do not advance MEDRE's immutable compatibility pin or
advertise native replies until a separate release review approves the results.

Mixed-installation restore, sustained BLE/radio tests and LXMF direct-link
investigation remain independent release gates; this campaign does not qualify
them or change the transport retry policy.

Choose the expected immutable revisions from the qualification plan independently
of the evidence record. The checker refuses records for other revisions, unknown
fields, duplicate JSON keys and noncanonical profile tags. Evidence references
are bounded printable labels; the tool does not fetch them, establish their
redaction, or verify their contents. Input is read with a 1 MiB bound and malformed
or excessively nested JSON is refused without printing the submitted data.
