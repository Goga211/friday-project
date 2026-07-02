from types import SimpleNamespace
from typing import Any

import pytest

from friday.core.brain import Brain


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


@pytest.mark.asyncio
async def test_dialog_history_carries_between_requests() -> None:
    """Второй запрос видит первую пару реплик — «а теперь закрой его» работает."""
    responses = [
        SimpleNamespace(stop_reason="end_turn", content=[_text("Открыл ютуб")]),
        SimpleNamespace(stop_reason="end_turn", content=[_text("Закрыл")]),
    ]
    client = _FakeClient(responses)
    brain = Brain(client, model="claude-haiku-4-5")

    await brain.handle("открой ютуб", _FakeRouter())  # type: ignore[arg-type]
    await brain.handle("а теперь закрой его", _FakeRouter())  # type: ignore[arg-type]

    second_call_messages = client.messages.calls[1]["messages"]
    assert second_call_messages == [
        {"role": "user", "content": "открой ютуб"},
        {"role": "assistant", "content": "Открыл ютуб"},
        {"role": "user", "content": "а теперь закрой его"},
    ]


@pytest.mark.asyncio
async def test_history_trimmed_and_starts_with_user() -> None:
    """История не растёт бесконечно и после среза начинается с user-реплики."""
    responses = [
        SimpleNamespace(stop_reason="end_turn", content=[_text(f"ответ {i}")]) for i in range(5)
    ]
    client = _FakeClient(responses)
    brain = Brain(client, model="claude-haiku-4-5", history_max_messages=4)

    for i in range(5):
        await brain.handle(f"вопрос {i}", _FakeRouter())  # type: ignore[arg-type]

    last_messages = client.messages.calls[-1]["messages"]
    # ≤ лимита истории + текущая фраза, и первая реплика — от user
    assert len(last_messages) <= 4 + 1
    assert last_messages[0]["role"] == "user"


@pytest.mark.asyncio
async def test_preload_history_restores_context() -> None:
    responses = [SimpleNamespace(stop_reason="end_turn", content=[_text("Помню")])]
    client = _FakeClient(responses)
    brain = Brain(client, model="claude-haiku-4-5")
    brain.preload_history([("user", "меня зовут Гога"), ("assistant", "Приятно познакомиться")])

    await brain.handle("как меня зовут?", _FakeRouter())  # type: ignore[arg-type]

    messages = client.messages.calls[0]["messages"]
    assert messages[0] == {"role": "user", "content": "меня зовут Гога"}


@pytest.mark.asyncio
async def test_reset_clears_history() -> None:
    responses = [
        SimpleNamespace(stop_reason="end_turn", content=[_text("ок")]),
        SimpleNamespace(stop_reason="end_turn", content=[_text("с чистого листа")]),
    ]
    client = _FakeClient(responses)
    brain = Brain(client, model="claude-haiku-4-5")
    await brain.handle("запомни: пароль 123", _FakeRouter())  # type: ignore[arg-type]
    brain.reset()
    await brain.handle("что я говорил?", _FakeRouter())  # type: ignore[arg-type]

    assert client.messages.calls[1]["messages"] == [{"role": "user", "content": "что я говорил?"}]


@pytest.mark.asyncio
async def test_system_block_has_cache_control() -> None:
    """system уходит блоком с cache_control — prompt caching (чтение 0.1× цены)."""
    responses = [SimpleNamespace(stop_reason="end_turn", content=[_text("Привет!")])]
    client = _FakeClient(responses)
    brain = Brain(client, model="claude-haiku-4-5")
    await brain.handle("привет", _FakeRouter())  # type: ignore[arg-type]

    system = client.messages.calls[0]["system"]
    assert isinstance(system, list)
    assert system[-1]["cache_control"] == {"type": "ephemeral"}
