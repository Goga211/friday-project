import pytest

from christopher.core.registry import DeviceRegistry
from christopher.core.router import ToolRouter
from christopher.shared.protocol import (
    Capability,
    CapabilityManifest,
    PendingAction,
    Response,
    RiskLevel,
)


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


@pytest.mark.asyncio
async def test_risky_action_deferred_to_pending() -> None:
    reg = _registry_with(
        Capability(name="run_command", description="shell", risk=RiskLevel.dangerous)
    )
    calls: list[tuple[str, str, dict, bool]] = []

    async def _caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
        calls.append((dev, act, params, confirm))
        return Response(correlation_id="x", source=dev, ok=True)

    pending: list[PendingAction] = []
    out = await ToolRouter(reg, _caller).execute("run_command", {"command": "ls"}, pending)

    assert out["status"] == "confirmation_required"
    assert calls == []  # действие НЕ выполнено
    assert len(pending) == 1
    assert pending[0].action == "run_command"
    assert pending[0].risk is RiskLevel.dangerous


@pytest.mark.asyncio
async def test_execute_confirmed_runs_with_flag() -> None:
    reg = _registry_with(
        Capability(name="launch_app", description="запуск", risk=RiskLevel.confirm)
    )
    calls: list[tuple[str, str, dict, bool]] = []

    async def _caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
        calls.append((dev, act, params, confirm))
        return Response(correlation_id="x", source=dev, ok=True, result={"launched": True})

    pa = PendingAction(
        device_id="d1",
        action="launch_app",
        params={"name": "firefox"},
        risk=RiskLevel.confirm,
        summary="launch_app(name='firefox')",
    )
    out = await ToolRouter(reg, _caller).execute_confirmed(pa)

    assert out["ok"] is True
    assert calls == [("d1", "launch_app", {"name": "firefox"}, True)]  # requires_confirm=True


@pytest.mark.asyncio
async def test_safe_action_executes_even_with_pending_collector() -> None:
    reg = _registry_with(Capability(name="ping", description="живость", risk=RiskLevel.safe))

    async def _caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
        return Response(correlation_id="x", source=dev, ok=True, result={"pong": True})

    pending: list[PendingAction] = []
    out = await ToolRouter(reg, _caller).execute("ping", {}, pending)
    assert out["ok"] is True
    assert pending == []  # safe не требует подтверждения


@pytest.mark.asyncio
async def test_local_tool_registered_and_executed() -> None:
    reg = DeviceRegistry()

    async def _caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
        raise AssertionError("локальный инструмент не должен выходить на шину")

    router = ToolRouter(reg, _caller)

    async def _handler(params: dict) -> dict:
        return {"echo": params.get("x")}

    router.register_local(
        Capability(name="list_actions", description="список", risk=RiskLevel.safe), _handler
    )

    assert any(t["name"] == "list_actions" for t in router.tool_definitions())
    out = await router.execute("list_actions", {"x": 42})
    assert out["ok"] is True
    assert out["result"] == {"echo": 42}
