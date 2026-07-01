import pytest

from christopher.core.registry import DeviceRegistry
from christopher.core.router import ToolRouter
from christopher.shared.protocol import Capability, CapabilityManifest, Response, RiskLevel


def _registry_with(*caps: Capability) -> DeviceRegistry:
    reg = DeviceRegistry()
    reg.update(CapabilityManifest(device_id="d1", platform="linux", capabilities=list(caps)))
    return reg


def test_tool_definitions_shape() -> None:
    reg = _registry_with(
        Capability(name="system_info", description="инфо о системе", risk=RiskLevel.safe)
    )

    async def _caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
        return Response(correlation_id="x", source=dev, ok=True)

    tools = ToolRouter(reg, _caller).tool_definitions()
    assert len(tools) == 1
    assert tools[0]["name"] == "system_info"
    assert tools[0]["input_schema"]["type"] == "object"


def test_tool_definitions_marks_risky() -> None:
    reg = _registry_with(
        Capability(name="run_command", description="выполнить", risk=RiskLevel.dangerous)
    )

    async def _caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
        return Response(correlation_id="x", source=dev, ok=True)

    tools = ToolRouter(reg, _caller).tool_definitions()
    assert "подтверждения" in tools[0]["description"]


@pytest.mark.asyncio
async def test_execute_routes_to_device() -> None:
    reg = _registry_with(Capability(name="ping", description="живость", risk=RiskLevel.safe))
    calls: list[tuple[str, str, dict, bool]] = []

    async def _caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
        calls.append((dev, act, params, confirm))
        return Response(correlation_id="x", source=dev, ok=True, result={"pong": True})

    out = await ToolRouter(reg, _caller).execute("ping", {})
    assert out["ok"] is True
    assert out["result"] == {"pong": True}
    assert calls[0][0] == "d1"


@pytest.mark.asyncio
async def test_execute_unknown_action() -> None:
    reg = _registry_with(Capability(name="ping", description="живость"))

    async def _caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
        raise AssertionError("не должно вызываться")

    out = await ToolRouter(reg, _caller).execute("nope", {})
    assert out["ok"] is False
    assert out["error"] is not None
