from types import SimpleNamespace
from typing import Any

import pytest

from christopher.core.brain import Brain


class _FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _FakeMessages(responses)


class _FakeRouter:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "system_info",
                "description": "инфо",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    async def execute(
        self, action: str, params: dict[str, Any], pending: Any = None
    ) -> dict[str, Any]:
        self.executed.append((action, params))
        return {"ok": True, "result": {"hostname": "gogabook"}}


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use(block_id: str, name: str, tool_input: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


@pytest.mark.asyncio
async def test_direct_answer_without_tools() -> None:
    responses = [SimpleNamespace(stop_reason="end_turn", content=[_text("Привет!")])]
    brain = Brain(_FakeClient(responses), model="claude-haiku-4-5", max_iterations=5)
    out = await brain.handle("привет", _FakeRouter())  # type: ignore[arg-type]
    assert out.text == "Привет!"
    assert out.pending == []


@pytest.mark.asyncio
async def test_tool_use_loop() -> None:
    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_tool_use("t1", "system_info", {})]),
        SimpleNamespace(stop_reason="end_turn", content=[_text("Хост gogabook")]),
    ]
    router = _FakeRouter()
    brain = Brain(_FakeClient(responses), model="claude-haiku-4-5", max_iterations=5)
    out = await brain.handle("покажи систему", router)  # type: ignore[arg-type]
    assert out.text == "Хост gogabook"
    assert router.executed == [("system_info", {})]


@pytest.mark.asyncio
async def test_iteration_limit() -> None:
    # всегда просит инструмент → упрёмся в лимит шагов
    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_tool_use(f"t{i}", "system_info", {})])
        for i in range(10)
    ]
    brain = Brain(_FakeClient(responses), model="claude-haiku-4-5", max_iterations=3)
    out = await brain.handle("зациклись", _FakeRouter())  # type: ignore[arg-type]
    assert "шаг" in out.text.lower()
