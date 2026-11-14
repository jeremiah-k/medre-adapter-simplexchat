"""Plain-text compatibility rendering using MEDRE attribution and fallbacks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from medre.adapters.metadata import project_source_fields
from medre.core.events.canonical import CanonicalEvent
from medre.core.events.text import select_message_body
from medre.core.rendering.attribution import (
    resolve_relay_attribution,
    format_relay_prefix,
)
from medre.core.rendering.relations import (
    degrade_relations_inline,
    destination_native_message_id,
)
from medre.core.rendering.renderer import RenderingContext, RenderingResult

from .config import SimplexChatConfig, plain_text


class SimplexChatRenderer:
    name = "simplexchat"

    def __init__(
        self,
        configs: Mapping[str, SimplexChatConfig],
        source_attribution: Mapping[str, Any] | None = None,
    ) -> None:
        self.configs = dict(configs)
        self.source_attribution = dict(source_attribution or {})

    def can_render(self, event: CanonicalEvent, ctx: RenderingContext) -> bool:
        return (
            ctx.target_platform == "simplexchat"
            and ctx.target_adapter in self.configs
            and ctx.delivery_strategy != "skip"
            and (
                event.event_kind in {"message.created", "message.text"}
                or (ctx.delivery_strategy == "fallback_text" and bool(event.relations))
            )
        )

    async def render(
        self, event: CanonicalEvent, ctx: RenderingContext
    ) -> RenderingResult:
        if not self.can_render(event, ctx):
            raise ValueError("Unsupported SimplexChat rendering target or strategy")
        config = self.configs[ctx.target_adapter]
        if (
            ctx.target_destination is not None
            and ctx.target_destination.kind != "channel"
        ):
            raise ValueError(
                "SimplexChat rendering requires a configured chat-ID channel"
            )
        destination = config.destination(ctx.target_channel)
        text = select_message_body(event.payload)
        fallback = ctx.delivery_strategy == "fallback_text"
        replies = [rel for rel in event.relations if rel.relation_type == "reply"]
        reply_id = None
        if not fallback and len(replies) == 1 and replies[0].target_event_id:
            candidate = destination_native_message_id(
                replies[0], ctx.target_adapter, ctx.target_channel, require_fact=True
            )
            if candidate is not None and plain_text(candidate):
                reply_id = candidate
        text = degrade_relations_inline(
            event, text, represented=(replies[0],) if reply_id is not None else ()
        )
        if not text.strip():
            raise ValueError("SimplexChat message body must not be empty")
        attr_config = self.source_attribution.get(event.source_adapter)
        native = dict(event.metadata.native.data) if event.metadata.native else {}
        projected = project_source_fields(
            native,
            source_adapter=event.source_adapter,
            source_transport_id=event.source_transport_id,
            platform_hint=getattr(attr_config, "platform", None),
        )
        attr = resolve_relay_attribution(
            event,
            source_origin_label=ctx.source_origin_label,
            configured_origin_label=getattr(attr_config, "origin_label", None),
            source_attribution=ctx.source_attribution,
            projected_fields=projected,
        )
        prefix = format_relay_prefix(config.relay_prefix, attr)
        if prefix.formatting_error:
            raise ValueError("Invalid SimplexChat relay prefix template")
        text = prefix.rendered_prefix + text
        if not text.strip() or not plain_text(text):
            raise ValueError("SimplexChat message requires non-empty text")
        # Fail rather than silently cut an identity prefix or message content.
        limits = [config.max_text_bytes]
        if ctx.max_text_bytes is not None:
            limits.append(ctx.max_text_bytes)
        if len(text.encode("utf-8")) > min(limits) or (
            ctx.max_text_chars is not None and len(text) > ctx.max_text_chars
        ):
            raise ValueError("Rendered SimplexChat text exceeds the destination budget")
        payload: dict[str, Any] = {
            "text": text,
            "chat_type": destination[0],
            "chat_id": destination[1],
        }
        reply_fallback = False
        if replies and not fallback:
            fallback_body = prefix.rendered_prefix + degrade_relations_inline(
                event, select_message_body(event.payload)
            )
            if (
                not plain_text(fallback_body)
                or not fallback_body.strip()
                or len(fallback_body.encode("utf-8")) > min(limits)
                or (
                    ctx.max_text_chars is not None
                    and len(fallback_body) > ctx.max_text_chars
                )
            ):
                raise ValueError(
                    "SimpleX reply fallback exceeds the destination budget"
                )
            if reply_id is not None:
                payload["reply_native_ref"] = reply_id
                payload["reply_fallback_text"] = fallback_body
            else:
                payload["text"] = fallback_body
                reply_fallback = True
        return RenderingResult(
            event_id=event.event_id,
            target_adapter=ctx.target_adapter,
            target_channel=ctx.target_channel,
            payload=payload,
            metadata={
                "simplexchat": {"format": "plain", "attribution": "relay"},
                **(
                    {
                        "relay_prefix_template": prefix.template_used,
                        "relay_prefix_rendered": prefix.rendered_prefix,
                        "relay_prefix_variables_used": prefix.variables_used,
                        "relay_prefix_missing_variables": prefix.missing_variables,
                        "relay_prefix_unknown_variables": prefix.unknown_variables,
                        "relay_prefix_formatting_error": prefix.formatting_error,
                    }
                    if config.relay_prefix
                    else {}
                ),
            },
            fallback_applied=(
                "strategy_fallback_text"
                if fallback
                else "relation_reply" if reply_fallback else None
            ),
        )


def build_renderer(
    *,
    runtime_configs: Mapping[str, Any],
    all_runtime_configs: Mapping[str, Any],
    source_attribution: Mapping[str, Any],
) -> SimplexChatRenderer | None:
    del all_runtime_configs
    configs = {
        rtc.adapter_id: rtc.config
        for rtc in runtime_configs.values()
        if rtc.enabled and rtc.config is not None
    }
    return SimplexChatRenderer(configs, source_attribution) if configs else None
