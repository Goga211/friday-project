from friday.shared.topics import (
    cmd_topic,
    device_from_registry_topic,
    registry_topic,
    resp_topic,
)


def test_topic_builders() -> None:
    assert cmd_topic("d1") == "friday/cmd/d1"
    assert resp_topic("abc") == "friday/resp/abc"
    assert registry_topic("d1") == "friday/registry/d1"


def test_device_from_registry_topic() -> None:
    assert device_from_registry_topic("friday/registry/desktop-pc") == "desktop-pc"
