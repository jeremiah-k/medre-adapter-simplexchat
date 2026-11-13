"""Relay prefix surface: template validation and outbound attribution."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from medre.adapters.simplexchat.codec import SimplexChatCodec
from medre.adapters.simplexchat.config import SimplexChatConfig
from medre.adapters.simplexchat.renderer import SimplexChatRenderer
from medre.core.rendering.renderer import RenderingContext

from conftest import info, item

DEFAULT_RELAY_PREFIX = "[{origin_label}] {sender}: "


def render_context():
    return RenderingContext(
        target_adapter="sx",
        target_channel="group:1",
        target_platform="simplexchat",
        delivery_strategy="direct",
    )


def decoded_event(config):
    return SimplexChatCodec(config, "profile").decode(info(), item())


def prefixed_renderer(config, relay_prefix):
    return SimplexChatRenderer(
        {"sx": replace(config, relay_prefix=relay_prefix)},
        {"sx": SimpleNamespace(platform="simplexchat", origin_label="Mesh")},
    )


@pytest.mark.parametrize(
    "template",
    [
        "{sender}: ",
        "{origin_label}|{sender} > ",
        "no placeholders at all",
    ],
)
def test_relay_prefix_accepts_custom_templates(config, template):
    assert replace(config, relay_prefix=template).validate().relay_prefix == template


def test_relay_prefix_default_template_is_pinned():
    assert SimplexChatConfig("sx").relay_prefix == DEFAULT_RELAY_PREFIX


def test_empty_relay_prefix_passes_validation(config):
    assert replace(config, relay_prefix="").validate().relay_prefix == ""


@pytest.mark.parametrize("value", [True, False, 7, None])
def test_relay_prefix_rejects_non_text_values(config, value):
    with pytest.raises(ValueError, match="relay_prefix must be valid text"):
        replace(config, relay_prefix=value).validate()


async def test_default_relay_prefix_prepends_resolved_variables(config):
    event = decoded_event(config)
    result = await prefixed_renderer(config, DEFAULT_RELAY_PREFIX).render(
        event, render_context()
    )
    assert result.payload["text"] == "[Mesh] Alice: Hello <SimpleX> & 世界"


async def test_empty_relay_prefix_renders_the_bare_body(config):
    event = decoded_event(config)
    result = await SimplexChatRenderer({"sx": config}).render(event, render_context())
    assert result.payload["text"] == event.payload["body"]
    assert event.payload["body"] == "Hello <SimpleX> & 世界"


async def test_custom_relay_prefix_template_resolves_variables(config):
    event = decoded_event(config)
    result = await prefixed_renderer(config, "{sender} via {origin_label} — ").render(
        event, render_context()
    )
    assert result.payload["text"] == "Alice via Mesh — Hello <SimpleX> & 世界"
