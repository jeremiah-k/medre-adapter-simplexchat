"""Explicit group/direct allowlists for one private native profile."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from medre.config.identifiers import adapter_id_problem
from medre.config.paths import MedrePaths


def conversation(value: str | None) -> tuple[str, int]:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:group|contact):[1-9][0-9]{0,18}", value
    ):
        raise ValueError("SimpleX channels must be group:ID or contact:ID")
    kind, number = value.split(":", 1)
    if int(number) > 2**63 - 1:
        raise ValueError("SimpleX conversation ID exceeds int64")
    return ("group" if kind == "group" else "direct", int(number))


def plain_text(value: object) -> bool:
    if not isinstance(value, str) or any(
        ord(char) < 32 and char not in "\n\r\t" for char in value
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


@dataclass(frozen=True)
class SimplexChatConfig:
    adapter_id: str
    database_prefix: str = ""
    database_key: str = ""
    user_id: int = 1
    chats: tuple[str, ...] = ()
    state_dir: str = ""
    native_cache_dir: str = ""
    libs_dir: str = ""
    command_timeout_seconds: float = 30.0
    startup_timeout_seconds: float = 90.0
    poll_interval_seconds: float = 1.0
    max_text_bytes: int = 16384
    origin_label: str = ""
    relay_prefix: str = "[{origin_label}] {sender}: "

    def validate(self) -> SimplexChatConfig:
        if problem := adapter_id_problem(self.adapter_id):
            raise ValueError(problem)
        if type(self.user_id) is not int or not 1 <= self.user_id <= 2**63 - 1:
            raise ValueError("user_id must be a positive native user ID")
        if (
            not isinstance(self.chats, tuple)
            or any(not isinstance(item, str) for item in self.chats)
            or len(set(self.chats)) != len(self.chats)
        ):
            raise ValueError("chats must be a tuple of distinct conversation channels")
        for channel in self.chats:
            conversation(channel)
        for name in (
            "database_prefix",
            "database_key",
            "state_dir",
            "native_cache_dir",
            "libs_dir",
            "origin_label",
            "relay_prefix",
        ):
            if not plain_text(getattr(self, name)):
                raise ValueError(f"{name} must be valid text")
        for name in (
            "command_timeout_seconds",
            "startup_timeout_seconds",
            "poll_interval_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive and finite")
        if type(self.max_text_bytes) is not int or self.max_text_bytes <= 0:
            raise ValueError("max_text_bytes must be a positive integer")
        return self

    def destination(self, channel: str | None) -> tuple[str, int]:
        result = conversation(channel)
        if channel not in self.chats:
            raise ValueError("SimpleX conversation is outside the configured allowlist")
        return result


def prepare_config(
    config: SimplexChatConfig,
    *,
    adapter_id: str,
    paths: MedrePaths,
    routes: Iterable[Any],
) -> SimplexChatConfig:
    root = paths.adapter_transport_state_dir(adapter_id, "simplexchat")
    changes = {}
    for name, default in (
        ("database_prefix", root / "profile"),
        ("state_dir", root),
        ("native_cache_dir", paths.cache_dir / "simplexchat"),
    ):
        value = getattr(config, name)
        changes[name] = str(paths.expand_placeholder(value)) if value else str(default)
    config = replace(config, **changes).validate()
    for route in routes:
        if not route.enabled:
            continue
        if route.source.adapter == adapter_id and route.source.channel is not None:
            config.destination(route.source.channel)
        for target in route.targets:
            if target.adapter == adapter_id:
                if (
                    target.destination is not None
                    and target.destination.kind != "channel"
                ):
                    raise ValueError("SimpleX destinations use conversation channels")
                config.destination(target.channel)
    return config
