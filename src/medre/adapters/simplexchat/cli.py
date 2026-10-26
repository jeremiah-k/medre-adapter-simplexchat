"""Explicit profile initialization and safe conversation discovery."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import math
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from medre.config.paths import resolve

from .config import SimplexChatConfig, plain_text, prepare_config
from .session import SessionError, SimplexChatSession


class UsageError(ValueError):
    """An operator error with a constant, safe-to-display explanation."""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="medre-simplexchat", description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("init", "Create an explicit private bot profile"),
        ("groups", "List group IDs and membership"),
        ("contacts", "List local direct conversation IDs"),
        ("join", "Connect using a private group/contact link"),
        ("create-group", "Create a bot-owned group"),
        ("accept-group", "Accept an existing group invitation"),
        ("connect", "Resume pending native connections for a bounded interval"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--adapter", default="simplexchat")
        command.add_argument(
            "--config", help="Read the selected adapter from a MEDRE YAML config"
        )
        command.add_argument(
            "--database-prefix", help="Override the private native profile prefix"
        )
        command.add_argument("--user-id", type=int)
        command.add_argument(
            "--libs-dir", help="Use a preinstalled official native library directory"
        )
        command.add_argument("--json", action="store_true")
        if name == "accept-group":
            command.add_argument("--group-id", type=int, required=True)
        if name in {"init", "create-group"}:
            command.add_argument("--name", default="MEDRE")
        if name in {"join", "accept-group", "connect"}:
            command.add_argument("--wait-seconds", type=float, default=60.0)
        if name == "join":
            command.add_argument(
                "--link-stdin",
                action="store_true",
                help="Read the private connection link from stdin",
            )
    return result


def selected_config(options: argparse.Namespace) -> SimplexChatConfig:
    paths = resolve()
    from medre.config.env import apply_env_overrides
    from medre.config.loader import load_config, parse_runtime_config

    if options.config:
        loaded, _, paths = load_config(options.config)
    else:
        loaded = parse_runtime_config(
            {"adapters": {"simplexchat": {options.adapter: {}}}, "routes": {}}, paths
        )
    loaded = apply_env_overrides(loaded)
    item = loaded.adapters.for_transport("simplexchat").get(options.adapter)
    if item is None:
        raise UsageError("The selected SimpleX adapter is absent from the config")
    config = item.config
    if not isinstance(config, SimplexChatConfig):
        raise UsageError("The selected SimpleX adapter is not a SimpleX configuration")
    changes = {
        name: getattr(options, name)
        for name in ("database_prefix", "user_id", "libs_dir")
        if getattr(options, name) is not None
    }
    config = replace(config, **changes)
    try:
        return prepare_config(
            config, adapter_id=config.adapter_id, paths=paths, routes=[]
        )
    except ValueError as exc:
        # Config validation emits field-specific, constant operator messages.
        # Do not echo arbitrary error contents from paths or native operations.
        message = str(exc)
        if message in {
            "SimpleX channels must be group:ID or contact:ID",
            "SimpleX conversation ID exceeds int64",
            "user_id must be a positive native user ID",
            "chats must be a tuple of distinct conversation channels",
            "max_text_bytes must be a positive integer",
        } or message.endswith((" must be valid text", " must be positive and finite")):
            raise UsageError(message) from None
        raise UsageError("Invalid SimpleX profile configuration") from None


# An unfinished CLI cleanup must not let Python collect its active profile
# lease while the interpreter remains alive. This is not a cross-process
# guardian if the CLI process itself is terminated.
_UNREAPED_SESSIONS: set[SimplexChatSession] = set()


async def execute(
    options: argparse.Namespace,
    config: SimplexChatConfig,
    *,
    session_factory: Callable[[SimplexChatConfig], SimplexChatSession] = (
        SimplexChatSession
    ),
) -> dict[str, Any] | list[dict[str, Any]]:
    if options.command != "init" and not await asyncio.to_thread(
        Path(config.database_prefix + "_chat.db").is_file
    ):
        raise UsageError("SimpleX profile is missing; run init explicitly")
    if options.command in {"init", "create-group"} and (
        not plain_text(options.name) or not options.name.strip()
    ):
        raise UsageError("The bot display name must be nonempty text")
    if options.command == "accept-group" and (
        type(options.group_id) is not int or options.group_id <= 0
    ):
        raise UsageError("--group-id must be positive")
    if options.command in {"join", "accept-group", "connect"} and (
        not math.isfinite(options.wait_seconds) or options.wait_seconds <= 0
    ):
        raise UsageError("--wait-seconds must be positive and finite")
    link: str | None = None
    if options.command == "join":
        if options.link_stdin:
            link = sys.stdin.readline().strip()
        elif sys.stdin.isatty():
            link = getpass.getpass("Private SimpleX connection link: ").strip()
        else:
            raise UsageError("Use --link-stdin for noninteractive connection setup")
        # Native SDK validates the invitation, including server-hosted links.
        if not link.startswith(("simplex:/", "https://")) or not plain_text(link):
            raise UsageError("Expected a SimpleX group/contact connection link")
    session = session_factory(config)
    try:
        await session.start(
            create_name=options.name if options.command == "init" else None
        )
        if options.command == "init":
            return {"user_id": config.user_id, "initialized": True}
        if options.command == "groups":
            groups = await session.call("groups")
            return [
                {
                    "channel": f"group:{group['groupId']}",
                    "name": group["localDisplayName"],
                    "membership": group["membership"]["memberStatus"],
                }
                for group in groups
            ]
        if options.command == "contacts":
            contacts = await session.call("contacts")
            return [
                {
                    "channel": f"contact:{contact['contactId']}",
                    "name": contact["localDisplayName"],
                    "status": contact["contactStatus"],
                }
                for contact in contacts
                if not contact.get("chatDeleted")
            ]
        if options.command == "create-group":
            await session.call("start")
            group = await session.call("new_group", name=options.name)
            return {
                "channel": f"group:{group['groupId']}",
                "name": group["localDisplayName"],
                "membership": group["membership"]["memberStatus"],
            }
        await session.call("start")
        if options.command == "join":
            await session.call("join", link=link)
        elif options.command == "accept-group":
            await session.call("accept_group", group_id=options.group_id)
        try:
            async with asyncio.timeout(options.wait_seconds):
                while not await session.call("connection_status"):
                    session.wake.clear()
                    try:
                        await asyncio.wait_for(session.wake.wait(), 1.0)
                    except TimeoutError:
                        pass
        except TimeoutError:
            return {
                "connection_requested": True,
                "connection_observed": False,
                "action": "Run medre-simplexchat connect to resume pending connections. Inspect groups; use accept-group for an invited membership.",
            }
        return {
            "connection_requested": True,
            "connection_observed": True,
            "action": "Inspect groups and contacts to select an active conversation. A connected contact does not prove group membership.",
        }
    finally:
        try:
            await session.stop()
        except BaseException:
            _UNREAPED_SESSIONS.add(session)
            raise
        else:
            _UNREAPED_SESSIONS.discard(session)


def _display(value: object) -> str:
    """Escape peer-controlled terminal control characters without losing IDs."""
    return "".join(ch if ch.isprintable() else f"\\u{ord(ch):04x}" for ch in str(value))


def main(argv: list[str] | None = None) -> int:
    options = parser().parse_args(argv)
    try:
        data = asyncio.run(execute(options, selected_config(options)))
    except (UsageError, SessionError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError:
        # Unknown ValueErrors may include a private invitation or profile key.
        print("Invalid SimpleX configuration", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        if str(exc) == "SimpleX profile is owned by another process or unavailable":
            print(str(exc), file=sys.stderr)
            return 1
        print("SimpleX runtime failed; inspect the native profile", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "SimpleX command interrupted; the native session has stopped.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        # Never print exception messages containing profile keys or invitation URIs.
        print(
            f"SimpleX command failed ({type(exc).__name__}); check the profile and configuration.",
            file=sys.stderr,
        )
        return 1
    if options.json:
        print(json.dumps(data, ensure_ascii=True))
    elif isinstance(data, list):
        for item in data:
            status = item.get("membership", item.get("status", ""))
            print(
                f"{_display(item['channel'])}  "
                f"{_display(item['name'])}  {_display(status)}"
            )
    else:
        print(json.dumps(data))
    return 0
