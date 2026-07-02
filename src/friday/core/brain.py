"""Мозг: диалоговый цикл с Claude (tool-use).

Принимает текст пользователя, отдаёт Claude инструменты из ToolRouter, крутит агентный цикл
(Claude → вызов инструмента → результат → …) до финального ответа или лимита шагов.
Локального LLM нет — только облако (Claude). Клиент типизирован как Any, чтобы модуль был
тестируемым с фейковым клиентом и не тянул тяжёлый импорт anthropic в тесты.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from friday.core.router import ToolRouter
from friday.shared.protocol import PendingAction

log = logging.getLogger("friday.brain")

SYSTEM_PROMPT = (
    "Ты — Пятница, AI-ассистент, который управляет устройствами пользователя.\n"
    "Отвечай кратко и по-русски. Если для ответа нужно действие на устройстве — вызывай "
    "инструмент.\n"
    "Не выдумывай результаты: если инструмент вернул ошибку — честно сообщи о ней.\n"
    "Если инструмент вернул status=confirmation_required — действие НЕ выполнено: коротко "
    "скажи пользователю, что нужно подтвердить, и не утверждай, что сделал это.\n"
    "После выполнения действия дай краткое подтверждение того, что сделано."
)


@dataclass(frozen=True)
class BrainResult:
    """Итог обработки запроса: текст ответа + risky-действия, ждущие подтверждения."""

    text: str
    pending: list[PendingAction] = field(default_factory=list)


def _final_text(response: Any) -> str:
    parts = [
        getattr(block, "text", "")
        for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    return "".join(parts).strip() or "(пустой ответ)"


class Brain:
    def __init__(
        self,
        client: Any,
        model: str,
        max_tokens: int = 2048,
        max_iterations: int = 8,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._max_iterations = max_iterations
        self._system = system_prompt

    async def handle(self, user_text: str, router: ToolRouter) -> BrainResult:
        tools = router.tool_definitions()
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_text}]
        pending: list[PendingAction] = []

        for _ in range(self._max_iterations):
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system,
                tools=tools,
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                return BrainResult(_final_text(response), pending)

            messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                result = await router.execute(block.name, dict(block.input), pending)
                log.info("инструмент %s → ok=%s", block.name, result.get("ok"))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        text = "Не удалось завершить за отведённое число шагов — попробуй переформулировать."
        return BrainResult(text, pending)
