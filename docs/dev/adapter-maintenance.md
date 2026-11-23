# Adapter maintenance

Maintainer-facing contracts for the SimpleX Chat adapter distribution. The
[README](../../README.md) keeps the operator journey; this page holds the
material behind it.

## Setup-guidance seam

`medre config native` offers no SimpleX form and never provisions this
transport. What it prints depends on the installed core revision:

- Cores without a typed setup-guidance seam refuse the transport with
  "'simplexchat' has no provider-declared native setup prompts; use its
  native configuration guide", pointing at this provider-owned guide.
- Cores with the seam render the provider's declared setup journey as an
  informational, offline listing of the same commands with per-step evidence
  notes, classified `informational_only`. The render exits without writing a
  configuration file, invokes nothing, and claims no provisioning: it is
  guidance, not setup.

Both behaviors route to the same journey in the README and to
`medre adapter simplexchat` for actual profile provisioning; core does not
invoke those commands automatically because they contact the native service,
unlike `config native`.

Core's `AdapterSpec` carries provider-owned setup surfaces: `cli_register`,
which exposes the provider's provisioning commands in the journey below;
`native_setup_fields`, an offline form that prompts for provider-declared
config values and writes a new YAML without contacting an SDK, account, or
device; and, on cores with the typed seam, `native_setup_instructions`, the
informational journey for form-less providers that this provider declares.
SimpleX profile, contact, and group provisioning cannot happen offline:
connection links and group membership require the native service. An offline
form could reference an existing profile, but could not provision it or
complete contact and group membership. The provider declares no such form;
it declares its journey as typed instructions so `medre config native` can
print exactly the commands in the README with evidence notes at their true
levels. A linked contact or a created group is not independently witnessed
delivery, and provisioning results are never relay acceptance, delivery, or
read evidence; they grant no send authority. The README remains the
configuration guide that older cores' refusal points to.
