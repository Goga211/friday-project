"""Тесты навыков desktop-агента (детерминированные, на Linux-утилитах из PATH)."""

from __future__ import annotations

import platform

import pytest

from christopher.agents.desktop import skills

_LINUX = platform.system() == "Linux"


@pytest.mark.asyncio
async def test_run_command_allowlist_rejects_dangerous() -> None:
    with pytest.raises(PermissionError):
        await skills.run_command({"command": "rm -rf /"})


@pytest.mark.asyncio
async def test_run_command_allowlist_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHRISTOPHER_CMD_ALLOWLIST", "echo")
    with pytest.raises(PermissionError):
        await skills.run_command({"command": "ls -la"})  # ls вне явного allowlist


@pytest.mark.asyncio
async def test_run_command_echo_succeeds() -> None:
    out = await skills.run_command({"command": "echo привет"})
    assert out["exit_code"] == 0
    assert "привет" in out["stdout"]


@pytest.mark.asyncio
async def test_run_command_empty_rejected() -> None:
    with pytest.raises(ValueError):
        await skills.run_command({"command": "   "})


@pytest.mark.asyncio
async def test_open_url_rejects_non_http() -> None:
    with pytest.raises(ValueError):
        await skills.open_url({"url": "ftp://example.com"})
    with pytest.raises(ValueError):
        await skills.open_url({})


@pytest.mark.asyncio
@pytest.mark.skipif(not _LINUX, reason="launch_app реализован под Linux")
async def test_launch_app_missing_binary() -> None:
    with pytest.raises(RuntimeError):
        await skills.launch_app({"name": "no_such_binary_xyz_123"})
