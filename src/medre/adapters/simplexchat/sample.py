from medre.adapter_spec import AdapterSample


def sample_config() -> AdapterSample:
    return AdapterSample(
        {"user_id": 1, "chats": ["group:1"], "origin_label": "SimpleX Chat"},
        channel="group:1",
    )
