from friday.shared.protocol import (
    Capability,
    CapabilityManifest,
    Command,
    Response,
    RiskLevel,
)


def test_command_roundtrip() -> None:
    cmd = Command(source="core", target="desktop-pc", action="ping")
    restored = Command.model_validate_json(cmd.model_dump_json())
    assert restored.action == "ping"
    assert restored.id == cmd.id
    assert restored.requires_confirm is False


def test_response_for_command() -> None:
    cmd = Command(source="core", target="d", action="system_info")
    resp = Response(correlation_id=cmd.id, source="d", ok=True, result={"x": 1})
    restored = Response.model_validate_json(resp.model_dump_json())
    assert restored.correlation_id == cmd.id
    assert restored.ok is True
    assert restored.result == {"x": 1}


def test_manifest_roundtrip() -> None:
    manifest = CapabilityManifest(
        device_id="d",
        platform="linux",
        capabilities=[Capability(name="ping", description="живость", risk=RiskLevel.safe)],
    )
    restored = CapabilityManifest.model_validate_json(manifest.model_dump_json())
    assert restored.online is True
    assert restored.capabilities[0].risk is RiskLevel.safe


def test_manifest_alias_and_mac_roundtrip() -> None:
    manifest = CapabilityManifest(
        device_id="d", platform="linux", alias="ноутбук", mac="AA:BB:CC:DD:EE:FF"
    )
    restored = CapabilityManifest.model_validate_json(manifest.model_dump_json())
    assert restored.alias == "ноутбук"
    assert restored.mac == "AA:BB:CC:DD:EE:FF"


def test_manifest_without_alias_mac_still_valid() -> None:
    # Совместимость: старые retained-манифесты без новых полей должны валидироваться.
    old_json = '{"device_id": "d", "platform": "linux"}'
    restored = CapabilityManifest.model_validate_json(old_json)
    assert restored.alias is None
    assert restored.mac is None
