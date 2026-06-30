from christopher.shared.protocol import (
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
