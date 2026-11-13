# Cover the relay prefix surface with tests

Add `tests/test_relay_prefix_surface.py` pinning the outbound attribution
surface that previously had no direct coverage. Validation tests pin the
default `"[{origin_label}] {sender}: "` template, acceptance of custom and
empty templates, and the `ValueError("relay_prefix must be valid text")`
rejection of boolean, integer, and `None` values. Rendering tests pin that
the renderer prepends the template resolved through the shared attribution
projection (`[Mesh] Alice: ...`), that an empty template relays the bare
body, and that a custom `{sender}`/`{origin_label}` template interpolates
both variables.

Test-only change: no adapter, schema, or specification behavior is altered.
