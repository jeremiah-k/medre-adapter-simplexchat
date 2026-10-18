import pytest

from medre.adapters.simplexchat.worker import NativeEvents

from conftest import info, item


def event(number=1, user=1, **updates):
    return {
        "type": "newChatItems",
        "user": {"userId": user},
        "chatItems": [{"chatInfo": info(), "chatItem": item(number, **updates)}],
    }


def test_buffer_enforces_native_user_allowlist_and_received_text_only():
    buffer = NativeEvents(1, ["group:1"])
    assert not buffer.feed({"type": "chatItemUpdated"})
    assert not buffer.feed(event(user=2))
    assert not buffer.feed(event(content={"type": "sndMsgContent"}))
    assert not buffer.feed(event(file={"fileId": 3}))
    assert not buffer.feed(
        event(content={"type": "rcvMsgContent", "msgContent": {"type": "image"}})
    )
    other = event()
    other["chatItems"][0]["chatInfo"]["groupInfo"]["groupId"] = 2
    assert not buffer.feed(other)
    assert buffer.feed(event())
    assert len(buffer.take()) == 1
    assert buffer.size == 0


def test_buffer_refuses_overflow_without_silently_discarding_items():
    buffer = NativeEvents(1, ["group:1"])
    for number in range(256):
        buffer.feed(event(number + 1))
    with pytest.raises(RuntimeError, match="overflow"):
        buffer.feed(event(257))
    ids = []
    for _ in range(8):
        rows = buffer.take()
        assert len(rows) == 32
        ids.extend(row["chatItem"]["meta"]["itemId"] for row in rows)
    assert ids == list(range(1, 257))
    assert not buffer.take()
    assert buffer.size == 0


def test_buffer_bounds_serialized_bytes_independently_of_item_count():
    buffer = NativeEvents(1, ["group:1"])
    with pytest.raises(RuntimeError, match="overflow"):
        buffer.feed(
            event(
                content={
                    "type": "rcvMsgContent",
                    "msgContent": {"type": "text", "text": "x" * (1024 * 1024)},
                }
            )
        )
    assert buffer.size == 0
    assert not buffer.records


def test_malformed_native_user_cannot_alias_an_allowed_user():
    with pytest.raises(ValueError, match="identity"):
        NativeEvents(1, ["group:1"]).feed(event(user=True))
