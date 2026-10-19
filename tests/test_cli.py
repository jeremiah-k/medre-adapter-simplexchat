import pytest

from medre.adapters.simplexchat import cli


class Profile:
    def __init__(self, config):
        self.closed = False
        self.calls = []

    async def start(self, **kwargs):
        self.calls.append(("open", kwargs))

    async def stop(self):
        self.closed = True

    async def call(self, op, **args):
        self.calls.append((op, args))
        if op == "groups":
            return [
                {
                    "groupId": 1,
                    "localDisplayName": "Group",
                    "membership": {"memberStatus": "connected"},
                    "privateKey": "must-not-export",
                }
            ]
        if op == "contacts":
            return [
                {
                    "contactId": 2,
                    "localDisplayName": "Bob",
                    "contactStatus": "active",
                    "privateKey": "must-not-export",
                }
            ]
        if op == "new_group":
            return {
                "groupId": 1,
                "localDisplayName": args["name"],
                "membership": {"memberStatus": "creator"},
            }
        if op == "connection_status":
            return True
        return None


@pytest.mark.parametrize("command", ["groups", "contacts", "create-group"])
async def test_discovery_returns_only_public_labels_and_local_ids(config, command):
    options = cli.parser().parse_args([command])
    session = Profile(config)
    data = await cli.execute(options, config, session_factory=lambda _: session)
    assert "must-not-export" not in repr(data)
    assert session.closed


async def test_join_reads_private_link_from_stdin_and_reports_observation(
    config, monkeypatch
):
    import io

    monkeypatch.setattr(
        cli.sys, "stdin", io.StringIO("https://simplex.chat/contact#private-token\n")
    )
    session = Profile(config)
    data = await cli.execute(
        cli.parser().parse_args(["join", "--link-stdin"]),
        config,
        session_factory=lambda _: session,
    )
    assert data["connection_observed"] is True
    assert "private-token" not in repr(data)
    assert session.closed
    assert (
        "join",
        {"link": "https://simplex.chat/contact#private-token"},
    ) in session.calls


async def test_group_acceptance_is_explicit(config):
    session = Profile(config)
    data = await cli.execute(
        cli.parser().parse_args(["accept-group", "--group-id", "7"]),
        config,
        session_factory=lambda _: session,
    )
    assert ("accept_group", {"group_id": 7}) in session.calls
    assert "joined" not in data


async def test_join_timeout_retains_pending_connection_without_claiming_membership(
    config,
):
    import asyncio

    class Pending(Profile):
        wake = asyncio.Event()

        async def call(self, op, **args):
            if op == "connection_status":
                return False
            return await super().call(op, **args)

    session = Pending(config)
    data = await cli.execute(
        cli.parser().parse_args(["connect", "--wait-seconds", "0.01"]),
        config,
        session_factory=lambda _: session,
    )
    assert data["connection_observed"] is False
    assert session.closed


def test_cli_failure_does_not_print_native_secrets(monkeypatch, capsys):
    def fail(options):
        raise RuntimeError("private-invitation-url")

    monkeypatch.setattr(cli, "selected_config", fail)
    assert cli.main(["groups"]) == 1
    assert "private-invitation-url" not in capsys.readouterr().err


def test_environment_secrets_apply_without_runtime_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDRE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEDRE_ADAPTER__SX__DATABASE_KEY", "private-key")
    config = cli.selected_config(cli.parser().parse_args(["init", "--adapter", "sx"]))
    assert config.database_key == "private-key"
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize(
    "args,message",
    [
        (["accept-group", "--group-id", "0"], "--group-id"),
        (["connect", "--wait-seconds", "0"], "--wait-seconds"),
        (["join", "--wait-seconds", "nan"], "--wait-seconds"),
    ],
)
async def test_invalid_inputs_refuse_before_start(config, args, message):
    started = []

    def factory(_):
        started.append(True)
        raise AssertionError("must not start native session")

    with pytest.raises(cli.UsageError, match=message):
        await cli.execute(
            cli.parser().parse_args(args), config, session_factory=factory
        )
    assert not started


async def test_short_link_uses_native_validation_and_is_read_before_session(
    config, monkeypatch
):
    import io

    monkeypatch.setattr(
        cli.sys, "stdin", io.StringIO("https://smp.example.org/join#private\n")
    )
    session = Profile(config)
    result = await cli.execute(
        cli.parser().parse_args(["join", "--link-stdin"]),
        config,
        session_factory=lambda _: session,
    )
    assert result["connection_observed"] is True
    assert ("join", {"link": "https://smp.example.org/join#private"}) in session.calls


def test_terminal_list_names_escape_controls(monkeypatch, capsys):
    async def fake_execute(*args, **kwargs):
        return [
            {"channel": "group:1", "name": "\x1b]0;danger\x07", "membership": "\x9b4m"}
        ]

    monkeypatch.setattr(cli, "selected_config", lambda _: object())
    monkeypatch.setattr(cli, "execute", fake_execute)
    assert cli.main(["groups"]) == 0
    result = capsys.readouterr().out
    assert "\\u001b" in result and "\\u009b" in result
    assert "\x1b" not in result and "\x9b" not in result


def test_profile_lease_operator_error_is_visible_but_unknown_runtime_is_masked(
    monkeypatch, capsys
):
    async def fail_lease(*args, **kwargs):
        raise RuntimeError("SimpleX profile is owned by another process or unavailable")

    monkeypatch.setattr(cli, "selected_config", lambda _: object())
    monkeypatch.setattr(cli, "execute", fail_lease)
    assert cli.main(["groups"]) == 1
    assert "owned by another process" in capsys.readouterr().err

    async def fail_secret(*args, **kwargs):
        raise RuntimeError("secret invite https://private.example")

    monkeypatch.setattr(cli, "execute", fail_secret)
    assert cli.main(["groups"]) == 1
    assert "private.example" not in capsys.readouterr().err


def test_json_discovery_escapes_c1_controls_without_changing_values(
    monkeypatch, capsys
):
    import json

    async def output(*args):
        return [
            {"channel": "group:1", "name": "Eve\u009b31m", "membership": "connected"}
        ]

    monkeypatch.setattr(cli, "selected_config", lambda _: None)
    monkeypatch.setattr(cli, "execute", output)
    assert cli.main(["groups", "--json"]) == 0
    text = capsys.readouterr().out
    assert "\u009b" not in text
    assert json.loads(text)[0]["name"] == "Eve\u009b31m"


def test_main_module_is_safe_to_import(monkeypatch, capsys):
    """Tooling can discover adapter modules without invoking the operator CLI."""
    import importlib
    import sys

    monkeypatch.setattr(sys, "argv", ["discovery", "--not-a-cli-option"])
    importlib.import_module("medre.adapters.simplexchat.__main__")
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err
