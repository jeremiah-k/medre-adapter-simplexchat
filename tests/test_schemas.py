"""Validate exported schemas against actual provider configurations and events."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from medre.adapters.simplexchat.codec import SimplexChatCodec
from medre.adapters.simplexchat.sample import sample_config
from conftest import info, item


def schema(name):
    data = json.loads((Path("docs/schemas") / name).read_text())
    Draft202012Validator.check_schema(data)
    return Draft202012Validator(data)


def test_outer_id_configuration_schema_accepts_the_generated_sample():
    validator = schema("config.schema.json")
    validator.validate(sample_config().config)
    validator.validate({"chats": ["group:1"], "adapter_kind": "fake"})
    with pytest.raises(ValidationError):
        validator.validate({"chats": ["group:0"]})
    with pytest.raises(ValidationError):
        validator.validate({"command_timeout_seconds": 0})


def test_native_schema_accepts_the_codec_projection(config):
    event = SimplexChatCodec(config, "profile").decode(info(), item())
    schema("native.schema.json").validate(event.metadata.native.data["simplexchat"])
