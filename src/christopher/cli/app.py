"""Интерактивный текстовый клиент.

Подключается к шине, шлёт запрос пользователя в Core (user/request) и ждёт ответ
(user/reply/<id>). Простой REPL: ввёл строку — получил ответ ассистента.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from christopher.shared.bus import Bus
from christopher.shared.config import BusSettings
from christopher.shared.protocol import AssistantReply, UserMessage
from christopher.shared.topics import USER_REPLY_WILDCARD, USER_REQUEST

log = logging.getLogger("christopher.cli")

CLI_ID = "christopher-cli"


async def _read_line() -> str:
    return await asyncio.to_thread(input, "\nты> ")


async def _await_reply(bus: Bus, correlation_id: str) -> str:
    async for message in bus.messages:
        payload = message.payload
        if not isinstance(payload, (bytes, bytearray)):
            continue
        reply = AssistantReply.model_validate_json(bytes(payload))
        if reply.correlation_id == correlation_id:
            return reply.text
    return "(соединение закрыто)"


async def run() -> None:
    settings = BusSettings()
    broker = f"{settings.broker_host}:{settings.broker_port}"
    print(f"Christopher CLI — брокер {broker}. Ctrl+C для выхода.")

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
            print(f"\nКристофер> {reply}")


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
