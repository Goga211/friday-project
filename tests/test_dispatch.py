import pytest

from friday.agents.desktop.capabilities import REGISTRY
from friday.shared.agent import dispatch
from friday.shared.protocol import Command


@pytest.mark.asyncio
async def test_ping_capability() -> None:
    _, handler = REGISTRY["ping"]
    assert await handler({}) == {"pong": True}


@pytest.mark.asyncio
async def test_system_info_capability() -> None:
    _, handler = REGISTRY["system_info"]
    info = await handler({})
    assert "hostname" in info
    assert "system" in info


@pytest.mark.asyncio
async def test_dispatch_known_action_ok() -> None:
    cmd = Command(source="core", target="d", action="ping")
    resp = await dispatch(cmd, REGISTRY)
    assert resp.ok is True
    assert resp.result == {"pong": True}
    assert resp.correlation_id == cmd.id


@pytest.mark.asyncio
async def test_dispatch_unknown_action_fails() -> None:
    cmd = Command(source="core", target="d", action="does_not_exist")
    resp = await dispatch(cmd, REGISTRY)
    assert resp.ok is False
    assert resp.error is not None
