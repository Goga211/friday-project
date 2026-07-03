import pytest

from friday.core.registry import DeviceRegistry
from friday.core.router import ToolRouter
from friday.shared.protocol import (
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


async def _noop_caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
    return Response(correlation_id="x", source=dev, ok=True)


def test_resolve_target_by_capability_when_hint_bogus() -> None:
    # Планировщик: мозг подставил несуществующий target ("system") — резолвим по навыку.
    reg = _registry_with(Capability(name="notify", description="увед", risk=RiskLevel.safe))
    router = ToolRouter(reg, _noop_caller)
    assert router.resolve_target("system", "notify") == "d1"


def test_resolve_target_prefers_valid_hint() -> None:
    reg = DeviceRegistry()
    reg.update(
        CapabilityManifest(
            device_id="d1",
            platform="linux",
            capabilities=[Capability(name="notify", description="увед", risk=RiskLevel.safe)],
        )
    )
    reg.update(
        CapabilityManifest(
            device_id="d2",
            platform="linux",
            capabilities=[Capability(name="notify", description="увед", risk=RiskLevel.safe)],
        )
    )
    router = ToolRouter(reg, _noop_caller)
    # hint валиден (онлайн + есть навык) — используем именно его, а не первое попавшееся
    assert router.resolve_target("d2", "notify") == "d2"


def test_resolve_target_none_when_no_device() -> None:
    reg = DeviceRegistry()
    router = ToolRouter(reg, _noop_caller)
    assert router.resolve_target("system", "notify") is None


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


def _two_device_registry() -> DeviceRegistry:
    """Два устройства с одинаковой возможностью notify: ПК (d1) и ноутбук (d2)."""
    reg = DeviceRegistry()
    notify = Capability(name="notify", description="увед", risk=RiskLevel.safe)
    reg.update(
        CapabilityManifest(device_id="d1", platform="linux", alias="пк", capabilities=[notify])
    )
    reg.update(
        CapabilityManifest(
            device_id="d2", platform="windows", alias="ноутбук", capabilities=[notify]
        )
    )
    return reg


def test_tool_definitions_expose_device_param_and_providers() -> None:
    tools = ToolRouter(_two_device_registry(), _noop_caller).tool_definitions()
    assert len(tools) == 1  # возможность на двух устройствах — один инструмент
    tool = tools[0]
    assert "device" in tool["input_schema"]["properties"]
    assert "пк" in tool["description"] and "ноутбук" in tool["description"]


def test_tool_definitions_no_device_param_for_local() -> None:
    router = ToolRouter(DeviceRegistry(), _noop_caller)

    async def _handler(params: dict) -> dict:
        return {}

    router.register_local(Capability(name="list_actions", description="список"), _handler)
    (tool,) = router.tool_definitions()
    assert "device" not in tool["input_schema"]["properties"]


def test_device_injection_does_not_mutate_capability() -> None:
    # Схема в манифесте иммутабельна: инъекция device не должна протекать в Capability.
    cap = Capability(
        name="notify",
        description="увед",
        params_schema={"type": "object", "properties": {"title": {"type": "string"}}},
    )
    reg = DeviceRegistry()
    reg.update(CapabilityManifest(device_id="d1", platform="linux", capabilities=[cap]))
    ToolRouter(reg, _noop_caller).tool_definitions()
    assert "device" not in cap.params_schema["properties"]


@pytest.mark.asyncio
async def test_execute_routes_to_named_device_by_alias() -> None:
    calls: list[tuple[str, str, dict, bool]] = []

    async def _caller(dev: str, act: str, params: dict, confirm: bool) -> Response:
        calls.append((dev, act, params, confirm))
        return Response(correlation_id="x", source=dev, ok=True)

    out = await ToolRouter(_two_device_registry(), _caller).execute(
        "notify", {"device": "Ноутбук", "message": "привет"}
    )
    assert out["ok"] is True
    assert calls[0][0] == "d2"  # алиас без учёта регистра → нужное устройство
    assert "device" not in calls[0][2]  # до навыка служебный параметр не доходит


@pytest.mark.asyncio
async def test_execute_unknown_device() -> None:
    out = await ToolRouter(_two_device_registry(), _noop_caller).execute(
        "notify", {"device": "тостер"}
    )
    assert out["ok"] is False
    assert "тостер" in out["error"]


@pytest.mark.asyncio
async def test_execute_offline_device_suggests_wake() -> None:
    reg = _two_device_registry()
    offline = reg.get("d2")
    assert offline is not None
    reg.update(offline.manifest.model_copy(update={"online": False}))
    out = await ToolRouter(reg, _noop_caller).execute("notify", {"device": "ноутбук"})
    assert out["ok"] is False
    assert "wake_device" in out["error"]


@pytest.mark.asyncio
async def test_execute_device_without_capability() -> None:
    reg = _two_device_registry()
    reg.update(CapabilityManifest(device_id="d3", platform="linux", alias="сервер"))
    out = await ToolRouter(reg, _noop_caller).execute("notify", {"device": "сервер"})
    assert out["ok"] is False
    assert "не умеет" in out["error"]


def test_resolve_target_accepts_alias() -> None:
    router = ToolRouter(_two_device_registry(), _noop_caller)
    assert router.resolve_target("ноутбук", "notify") == "d2"


@pytest.mark.asyncio
async def test_pending_summary_names_device() -> None:
    reg = DeviceRegistry()
    reg.update(
        CapabilityManifest(
            device_id="d1",
            platform="linux",
            alias="пк",
            capabilities=[Capability(name="launch_app", description="зап", risk=RiskLevel.confirm)],
        )
    )
    pending: list[PendingAction] = []
    await ToolRouter(reg, _noop_caller).execute("launch_app", {"name": "firefox"}, pending)
    assert "пк" in pending[0].summary


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
