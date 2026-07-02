"""Тесты навыков desktop-агента (детерминированные, на Linux-утилитах из PATH)."""

from __future__ import annotations

import platform

import pytest

from friday.agents.desktop import skills

_LINUX = platform.system() == "Linux"


@pytest.mark.asyncio
async def test_run_command_allowlist_rejects_dangerous() -> None:
    with pytest.raises(PermissionError):
        await skills.run_command({"command": "rm -rf /"})


@pytest.mark.asyncio
async def test_run_command_allowlist_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRIDAY_CMD_ALLOWLIST", "echo")
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


@pytest.mark.asyncio
async def test_manage_window_validates_params() -> None:
    with pytest.raises(ValueError):
        await skills.manage_window({"title": "", "action": "close"})
    with pytest.raises(ValueError):
        await skills.manage_window({"title": "Firefox", "action": "explode"})


@pytest.mark.asyncio
async def test_focus_window_requires_title() -> None:
    with pytest.raises(ValueError):
        await skills.focus_window({})


@pytest.mark.asyncio
@pytest.mark.skipif(not _LINUX, reason="проверка Wayland-гварда актуальна для Linux")
async def test_window_skills_blocked_on_wayland(monkeypatch: pytest.MonkeyPatch) -> None:
    """На Wayland управление окнами честно отклоняется, а не молча не работает."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    with pytest.raises(RuntimeError, match="Wayland"):
        await skills.list_windows({})
    with pytest.raises(RuntimeError, match="Wayland"):
        await skills.focus_window({"title": "Firefox"})
    with pytest.raises(RuntimeError, match="Wayland"):
        await skills.manage_window({"title": "Firefox", "action": "minimize"})


def test_winctl_raises_off_windows() -> None:
    """Win32-обёртки на не-Windows падают с понятной ошибкой, а не с AttributeError."""
    from friday.agents.desktop import winctl

    if platform.system() == "Windows":
        pytest.skip("на Windows реальный вызов")
    with pytest.raises(RuntimeError, match="Windows"):
        winctl.list_windows()
    with pytest.raises(RuntimeError, match="Windows"):
        winctl.send_text("привет")


def test_ps_quote_escapes_single_quotes() -> None:
    assert skills.ps_quote("it's") == "'it''s'"
    assert skills.ps_quote("plain") == "'plain'"
