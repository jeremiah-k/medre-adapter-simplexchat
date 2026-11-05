# Declare the SimpleX provisioning journey as typed setup instructions

Reference the provider's staged README journey from the adapter spec through
the optional `native_setup_instructions` symbol on cores that provide the
provider-setup-instructions seam. The declaration carries seven ordered,
verbatim `medre adapter simplexchat` commands — init, join, groups listing,
accept-group, connect, create-group, and contacts — each with an evidence
note at its true level: a completed provisioning step is never relay
acceptance, delivery, or read evidence, grants no send authority, and a
linked contact or created group is not an independently witnessed delivery.

The declaration is informational only. The provider still declares no
`native_setup_fields` form, provisioning stays operator-owned, and core
never executes a declared command because they contact the native service.
On cores with the seam, `medre config native` renders the journey with the
`informational_only` classification without writing a configuration file;
older cores keep the provider-declared-prompts refusal that routes to this
guide, and the spec constructs identically when the symbol is absent.

Discovery also omits the declaration when the core spec model lacks its optional
instructions field, even if the journey value types are importable.
