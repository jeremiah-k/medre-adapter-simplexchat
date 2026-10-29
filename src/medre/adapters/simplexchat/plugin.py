"""Provider discovery stays independent of native initialization."""

from medre.adapter_spec import AdapterSpec, SymbolRef


def adapter_spec() -> AdapterSpec:
    module = "medre.adapters.simplexchat"
    return AdapterSpec(
        transport="simplexchat",
        config=SymbolRef(module + ".config", "SimplexChatConfig"),
        runtime_config=None,
        adapter=SymbolRef(module + ".adapter", "SimplexChatAdapter"),
        fake_adapter=SymbolRef(module + ".fake", "FakeSimplexChatAdapter"),
        renderer_factory=SymbolRef(module + ".renderer", "build_renderer"),
        runtime_config_preparer=SymbolRef(module + ".config", "prepare_config"),
        dependency_probe=SymbolRef(module + ".compat", "HAS_SIMPLEXCHAT"),
        distribution="simplex-chat",
        import_names=("simplex_chat",),
        sdk_import_roots=("simplex_chat",),
        native_namespace_reader=SymbolRef(module + ".metadata", "native_namespace"),
        versioned_namespace_reader=SymbolRef(module + ".metadata", "versioned"),
        attribution_projector=SymbolRef(module + ".metadata", "project_attribution"),
        sample_config=SymbolRef(module + ".sample", "sample_config"),
        stopped_state_declaration=SymbolRef(
            module + ".stopped_declaration", "declaration"
        ),
        cli_register=SymbolRef(
            "medre.adapters.simplexchat.cli_contrib", "register_cli"
        ),
        cli_dispatch=SymbolRef(
            "medre.adapters.simplexchat.cli_contrib", "dispatch_cli"
        ),
        support_endpoint_fields=("user_id", "chats"),
        support_secret_fields=(
            "database_prefix",
            "database_key",
            "state_dir",
            "native_cache_dir",
            "libs_dir",
        ),
    )
