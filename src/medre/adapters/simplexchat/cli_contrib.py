"""SDK-free parser contribution for standalone and MEDRE-managed commands."""

from __future__ import annotations

import argparse


def configure_parser(result: argparse.ArgumentParser, *, command_dest: str) -> None:
    """Share arguments while preserving the caller's dispatch namespace."""
    result.description = "Initialize SimpleX profiles and discover MEDRE conversations."
    commands = result.add_subparsers(dest=command_dest, required=True)
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


def register_cli(parser: argparse.ArgumentParser) -> None:
    """Configure the core-owned medre adapter simplexchat namespace without native I/O."""
    configure_parser(parser, command_dest="adapter_simplexchat_command")


def dispatch_cli(args: argparse.Namespace) -> bool:
    """Run the selected provider operation without altering core's parsed namespace."""
    if args.command != "adapter" or args.adapter_command != "simplexchat":
        return False
    from .cli import run

    options = argparse.Namespace(**vars(args))
    options.command = args.adapter_simplexchat_command
    raise SystemExit(run(options))
