"""Тесты Core-приложения на фейках: обработка запросов, подтверждения, scheduler-tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from friday.core.app import Core
from friday.core.brain import BrainResult
from friday.shared.config import BusSettings
from friday.shared.protocol import (
    Capability,
    CapabilityManifest,
    ConfirmDecision,
    Event,
    PendingAction,
    Response,
    RiskLevel,
    UserMessage,
)


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, BaseModel]] = []

    async def publish_model(
        self, topic: str, model: BaseModel, qos: int = 1, retain: bool = False
    ) -> None:
        self.published.append((topic, model))


class _FakeBrain:
    def __init__(self, result: BrainResult) -> None:
        self.result = result
        self.remembered: list[tuple[str, str]] = []

    async def handle(self, user_text: str, router: Any) -> BrainResult:
        return self.result

    def remember(self, user_text: str, reply: str) -> None:
        self.remembered.append((user_text, reply))


@pytest.fixture()
def core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Core:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    instance = Core(
        BusSettings(
            audit_db=str(tmp_path / "audit.db"),
            scheduler_db=str(tmp_path / "jobs.db"),
        )
    )
    instance._bus = _FakeBus()  # type: ignore[assignment]
    return instance


def _bus(core: Core) -> _FakeBus:
    return core._bus  # type: ignore[return-value]


def _manifest(device_id: str, *caps: Capability, online: bool = True) -> bytes:
    manifest = CapabilityManifest(
        device_id=device_id, platform="linux", online=online, capabilities=list(caps)
    )
    return manifest.model_dump_json().encode()


@pytest.mark.asyncio
async def test_user_request_without_brain_replies_gracefully(core: Core) -> None:
    msg = UserMessage(text="привет")
    await core._process_user_request(msg)

    topic, reply = _bus(core).published[-1]
    assert topic.endswith(msg.id)
    assert "ANTHROPIC_API_KEY" in getattr(reply, "text", "")


@pytest.mark.asyncio
async def test_user_request_with_pending_stores_confirm(core: Core) -> None:
    pending = PendingAction(
        device_id="core",
        action="noop",
        params={},
        risk=RiskLevel.confirm,
        summary="noop()",
    )
    core.brain = _FakeBrain(BrainResult("Нужно подтверждение", [pending]))  # type: ignore[assignment]

    msg = UserMessage(text="сделай рискованное")
    await core._process_user_request(msg)

    assert core._pending_confirm[msg.id] == [pending]
    _topic, reply = _bus(core).published[-1]
    assert getattr(reply, "pending", None) == [pending]


@pytest.mark.asyncio
async def test_confirm_approved_runs_local_action(core: Core) -> None:
    executed: list[dict[str, Any]] = []

    async def _handler(params: dict[str, Any]) -> dict[str, Any]:
        executed.append(params)
        return {"done": True}

    core.router.register_local(
        Capability(name="noop", description="тест", risk=RiskLevel.confirm), _handler
    )
    core.brain = _FakeBrain(BrainResult("не важно"))  # type: ignore[assignment]
    core._pending_confirm["req-1"] = [
        PendingAction(
            device_id="core",
            action="noop",
            params={"x": 1},
            risk=RiskLevel.confirm,
            summary="noop(x=1)",
        )
    ]

    await core._process_confirm(ConfirmDecision(reply_id="req-1", approved=True))

    assert executed == [{"x": 1}]
    _topic, reply = _bus(core).published[-1]
    assert "✓" in getattr(reply, "text", "")
    # итог подтверждения дописан в контекст мозга
    assert core.brain.remembered == [("да", getattr(reply, "text", ""))]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_confirm_declined_runs_nothing(core: Core) -> None:
    core._pending_confirm["req-2"] = [
        PendingAction(
            device_id="core", action="noop", params={}, risk=RiskLevel.confirm, summary="noop()"
        )
    ]
    await core._process_confirm(ConfirmDecision(reply_id="req-2", approved=False))

    _topic, reply = _bus(core).published[-1]
    assert "Отменено" in getattr(reply, "text", "")


@pytest.mark.asyncio
async def test_confirm_unknown_reply_id(core: Core) -> None:
    await core._process_confirm(ConfirmDecision(reply_id="нет-такого", approved=True))
    _topic, reply = _bus(core).published[-1]
    assert "устарело" in getattr(reply, "text", "")


def test_handle_manifest_updates_registry(core: Core) -> None:
    cap = Capability(name="ping", description="ping", risk=RiskLevel.safe)
    core._handle_manifest(_manifest("desktop-x", cap))
    assert "desktop-x" in core.registry.online_devices()

    core._handle_manifest(_manifest("desktop-x", cap, online=False))
    assert "desktop-x" not in core.registry.online_devices()


def test_handle_response_resolves_pending_future(core: Core) -> None:
    import asyncio

    async def run() -> None:
        future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        core._pending["cmd-1"] = future
        resp = Response(correlation_id="cmd-1", source="desktop-x", ok=True, result={"pong": 1})
        core._handle_response(resp.model_dump_json().encode())
        assert future.done()
        assert (await future).ok

    asyncio.run(run())


@pytest.mark.asyncio
async def test_scheduler_tools_roundtrip(core: Core) -> None:
    core._setup_scheduler()
    try:
        out = await core._tool_schedule_action(
            {"action": "notify", "params": {"message": "чай"}, "delay_seconds": 3600}
        )
        job_id = str(out["id"])
        assert (await core._tool_list_actions({}))["jobs"]
        assert (await core._tool_cancel_action({"id": job_id})) == {"cancelled": True}
        assert (await core._tool_list_actions({}))["jobs"] == []
    finally:
        assert core._scheduler is not None
        core._scheduler.shutdown()


@pytest.mark.asyncio
async def test_schedule_action_requires_action(core: Core) -> None:
    core._setup_scheduler()
    try:
        with pytest.raises(ValueError):
            await core._tool_schedule_action({"delay_seconds": 10})
    finally:
        assert core._scheduler is not None
        core._scheduler.shutdown()


@pytest.mark.asyncio
async def test_fire_scheduled_publishes_to_online_device(core: Core) -> None:
    cap = Capability(name="notify", description="уведомление", risk=RiskLevel.safe)
    core._handle_manifest(_manifest("desktop-x", cap))

    await core._fire_scheduled("", "notify", {"message": "чай готов"})

    topic, cmd = _bus(core).published[-1]
    assert topic == "friday/cmd/desktop-x"
    assert getattr(cmd, "action", None) == "notify"
    assert getattr(cmd, "requires_confirm", None) is True


@pytest.mark.asyncio
async def test_fire_scheduled_no_device_logs_not_crashes(core: Core) -> None:
    before = len(_bus(core).published)
    await core._fire_scheduled("", "notify", {})  # устройств нет — команда не отправлена
    assert len(_bus(core).published) == before


# --- устройства: list_devices и wake_device ---


@pytest.mark.asyncio
async def test_list_devices_shows_offline_with_alias(core: Core) -> None:
    manifest = CapabilityManifest(
        device_id="pc", platform="linux", online=False, alias="пк", mac="AA:BB:CC:DD:EE:FF"
    )
    core.registry.update(manifest)

    out = await core.router.execute("list_devices", {})
    assert out["ok"] is True
    devices = out["result"]["devices"]
    assert devices == [
        {"id": "pc", "alias": "пк", "platform": "linux", "online": False, "capabilities": []}
    ]


@pytest.mark.asyncio
async def test_wake_device_sends_magic_packet(core: Core, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "friday.core.app.send_magic_packet",
        lambda mac, broadcast, port: sent.append((mac, broadcast, port)),
    )
    core.registry.update(
        CapabilityManifest(
            device_id="pc", platform="linux", online=False, alias="пк", mac="AA:BB:CC:DD:EE:FF"
        )
    )

    out = await core._tool_wake_device({"device": "пк"})
    assert out["sent"] is True
    assert sent == [("AA:BB:CC:DD:EE:FF", "255.255.255.255", 9)]


@pytest.mark.asyncio
async def test_wake_device_already_online(core: Core) -> None:
    core.registry.update(
        CapabilityManifest(device_id="pc", platform="linux", online=True, alias="пк")
    )
    out = await core._tool_wake_device({"device": "пк"})
    assert out.get("already_online") is True


@pytest.mark.asyncio
async def test_wake_device_errors(core: Core) -> None:
    with pytest.raises(ValueError, match="неизвестное устройство"):
        await core._tool_wake_device({"device": "тостер"})
    core.registry.update(
        CapabilityManifest(device_id="pc", platform="linux", online=False, alias="пк")
    )
    with pytest.raises(ValueError, match="нет MAC"):
        await core._tool_wake_device({"device": "пк"})


@pytest.mark.asyncio
async def test_wake_device_is_risky_tool(core: Core) -> None:
    # wake_device — confirm: без подтверждения уходит в pending, параметр device сохраняется.
    core.registry.update(
        CapabilityManifest(device_id="pc", platform="linux", online=False, alias="пк")
    )
    pending: list[PendingAction] = []
    out = await core.router.execute("wake_device", {"device": "пк"}, pending)
    assert out["status"] == "confirmation_required"
    assert pending[0].params == {"device": "пк"}


@pytest.mark.asyncio
async def test_notify_phone_registered_only_with_push_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    base = dict(audit_db=str(tmp_path / "a.db"), scheduler_db=str(tmp_path / "j.db"))

    without = Core(BusSettings(**base, push_url=None))
    assert all(t["name"] != "notify_phone" for t in without.router.tool_definitions())

    with_url = Core(BusSettings(**base, push_url="https://ntfy.sh/friday-x"))
    assert any(t["name"] == "notify_phone" for t in with_url.router.tool_definitions())


# --- события агентов: итог фоновой задачи Claude Code ---


def _task_done_event(source: str, *, ok: bool, result: str) -> Event:
    return Event(
        source=source,
        type="claude_task_done",
        data={"task": "поправь код", "ok": ok, "result": result},
    )


@pytest.mark.asyncio
async def test_task_done_event_lands_in_brain_context(core: Core) -> None:
    core.registry.update(CapabilityManifest(device_id="desktop-pc", platform="linux", alias="пк"))
    brain = _FakeBrain(BrainResult(text="ок"))
    core.brain = brain  # type: ignore[assignment]

    await core._announce_task_result(_task_done_event("desktop-pc", ok=True, result="готово: 42"))

    assert brain.remembered, "итог задачи не попал в контекст мозга"
    _user, text = brain.remembered[0]
    assert "пк" in text and "выполнена" in text and "готово: 42" in text


@pytest.mark.asyncio
async def test_task_done_event_reports_failure(core: Core) -> None:
    brain = _FakeBrain(BrainResult(text="ок"))
    core.brain = brain  # type: ignore[assignment]

    await core._announce_task_result(
        _task_done_event("desktop-pc", ok=False, result="упало на тестах")
    )

    _user, text = brain.remembered[0]
    assert "ошибкой" in text and "упало на тестах" in text


@pytest.mark.asyncio
async def test_spawn_event_ignores_unknown_types(core: Core) -> None:
    event = Event(source="desktop-pc", type="что-то-другое", data={})
    core._spawn_event(event.model_dump_json().encode())
    assert not core._tasks, "неизвестное событие не должно порождать задачу"


@pytest.mark.asyncio
async def test_spawn_event_ignores_garbage(core: Core) -> None:
    core._spawn_event(b"{not json")
    assert not core._tasks
