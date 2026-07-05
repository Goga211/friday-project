"""Интерактивный текстовый клиент.

Подключается к шине, шлёт запрос пользователя в Core (user/request) и ждёт ответ
(user/reply/<id>). Простой REPL: ввёл строку — получил ответ ассистента.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from friday.shared import aio
from friday.shared.bus import Bus
from friday.shared.config import BusSettings
from friday.shared.protocol import AssistantReply, ConfirmDecision, UserMessage
from friday.shared.topics import USER_CONFIRM, USER_REPLY_WILDCARD, USER_REQUEST

log = logging.getLogger("friday.cli")

CLI_ID = "friday-cli"


async def _read_line(prompt: str = "\nты> ") -> str:
    return await asyncio.to_thread(input, prompt)


async def _await_reply(bus: Bus, correlation_id: str) -> AssistantReply | None:
    async for message in bus.messages:
        payload = message.payload
        if not isinstance(payload, (bytes, bytearray)):
            continue
        reply = AssistantReply.model_validate_json(bytes(payload))
        if reply.correlation_id == correlation_id:
            return reply
    return None


async def _confirm_flow(bus: Bus, reply: AssistantReply) -> None:
    print("\nТребуется подтверждение:")
    for pa in reply.pending:
        print(f"  • [{pa.risk.value}] {pa.summary}")
    answer = (await _read_line("Подтвердить? [y/N] ")).strip().lower()
    approved = answer in {"y", "yes", "д", "да"}
    await bus.publish_model(
        USER_CONFIRM, ConfirmDecision(reply_id=reply.correlation_id, approved=approved)
    )
    result = await _await_reply(bus, reply.correlation_id)
    print(f"\nПятница> {result.text if result else '(соединение закрыто)'}")


async def run() -> None:
    settings = BusSettings()
    broker = f"{settings.broker_host}:{settings.broker_port}"
    print(f"Пятница CLI — брокер {broker}. Ctrl+C для выхода.")

    async with Bus(settings, client_id=CLI_ID) as bus:
        await bus.subscribe(USER_REPLY_WILDCARD)
        while True:
            try:
                text = (await _read_line()).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            msg = UserMessage(text=text)
            await bus.publish_model(USER_REQUEST, msg)
            reply = await _await_reply(bus, msg.id)
            if reply is None:
                print("\n(соединение закрыто)")
                break
            print(f"\nПятница> {reply.text}")
            if reply.pending:
                await _confirm_flow(bus, reply)


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        aio.run(run())


if __name__ == "__main__":
    main()
