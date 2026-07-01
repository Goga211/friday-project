"""Голосовой агент на шине (Phase 2).

Собирает провайдеры (микрофон, wake-word, STT, TTS) через фабрику и крутит голосовой
пайплайн. Распознанную фразу отправляет мозгу как UserMessage (тот же путь, что у CLI:
user/request → user/reply), ответ произносит голосом. Дополнительно объявляет возможность
`say` — Core может через мозг проговорить произвольный текст на Hub'е. Манифест online/offline
и Last-Will — как у desktop-агента (видимость в реестре).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import platform

import aiomqtt

from christopher.agents.voice.config import VoiceSettings
from christopher.agents.voice.factory import (
    build_recognizer,
    build_sink,
    build_source,
    build_synthesizer,
    build_wake,
)
from christopher.agents.voice.interfaces import AudioSink, SpeechSynthesizer
from christopher.agents.voice.pipeline import VoicePipeline
from christopher.shared.bus import Bus
from christopher.shared.config import BusSettings
from christopher.shared.logging import setup_logging
from christopher.shared.protocol import (
    AssistantReply,
    Capability,
    CapabilityManifest,
    Command,
    Response,
    RiskLevel,
    UserMessage,
    VoiceSay,
    VoiceTranscript,
)
from christopher.shared.topics import (
    PREFIX,
    USER_REPLY_WILDCARD,
    USER_REQUEST,
    VOICE_SAY,
    VOICE_TRANSCRIPT,
    cmd_topic,
    registry_topic,
    resp_topic,
)

log = logging.getLogger("christopher.voice.app")

_SAY_CAPABILITY = Capability(
    name="say",
    description="Произнести текст голосом на Hub (params: text)",
    risk=RiskLevel.safe,
    params_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
)


def _default_device_id() -> str:
    return f"voice-{platform.node() or 'unknown'}"


def _build_manifest(device_id: str, online: bool) -> CapabilityManifest:
    return CapabilityManifest(
        device_id=device_id,
        platform=platform.system().lower(),
        online=online,
        capabilities=[_SAY_CAPABILITY],
    )


class VoiceApp:
    def __init__(self, bus_settings: BusSettings, voice_settings: VoiceSettings) -> None:
        self._bus_settings = bus_settings
        self._voice = voice_settings
        self._device_id = bus_settings.device_id or _default_device_id()
        self._bus: Bus | None = None
        self._synth: SpeechSynthesizer | None = None
        self._sink: AudioSink | None = None
        self._pending: dict[str, asyncio.Future[str]] = {}

    @property
    def _bus_or_raise(self) -> Bus:
        if self._bus is None:
            raise RuntimeError("VoiceApp: шина не подключена")
        return self._bus

    # --- колбэки пайплайна ---
    async def _on_transcript(self, text: str) -> str:
        msg = UserMessage(text=text)
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[msg.id] = future
        await self._bus_or_raise.publish_model(USER_REQUEST, msg)
        try:
            return await asyncio.wait_for(future, timeout=self._voice.reply_timeout)
        except TimeoutError:
            log.warning("мозг не ответил за %.0f с", self._voice.reply_timeout)
            return "Извини, я не успел получить ответ."
        finally:
            self._pending.pop(msg.id, None)

    async def _on_event(self, kind: str, text: str) -> None:
        if kind == "transcript":
            await self._bus_or_raise.publish_model(VOICE_TRANSCRIPT, VoiceTranscript(text=text))
        elif kind == "say":
            await self._bus_or_raise.publish_model(VOICE_SAY, VoiceSay(text=text))

    # --- входящие сообщения шины ---
    def _handle_reply(self, payload: bytes) -> None:
        reply = AssistantReply.model_validate_json(payload)
        future = self._pending.get(reply.correlation_id)
        if future is not None and not future.done():
            future.set_result(reply.text)

    async def _handle_command(self, payload: bytes) -> None:
        cmd = Command.model_validate_json(payload)
        resp = await self._dispatch_say(cmd)
        await self._bus_or_raise.publish_model(resp_topic(cmd.id), resp)

    async def _dispatch_say(self, cmd: Command) -> Response:
        if cmd.action != "say":
            return Response(
                correlation_id=cmd.id,
                source=self._device_id,
                ok=False,
                error=f"неизвестное действие: {cmd.action}",
            )
        text = str(cmd.params.get("text", "")).strip()
        if not text:
            return Response(
                correlation_id=cmd.id, source=self._device_id, ok=False, error="пустой текст"
            )
        if self._synth is None or self._sink is None:
            return Response(
                correlation_id=cmd.id, source=self._device_id, ok=False, error="TTS не готов"
            )
        try:
            await self._on_event("say", text)
            clip = await self._synth.synthesize(text)
            await self._sink.play(clip)
            return Response(
                correlation_id=cmd.id, source=self._device_id, ok=True, result={"said": text}
            )
        except Exception as exc:  # noqa: BLE001 — агент не должен падать на ошибке навыка
            log.exception("ошибка произнесения")
            return Response(correlation_id=cmd.id, source=self._device_id, ok=False, error=str(exc))

    async def _consume(self) -> None:
        async for message in self._bus_or_raise.messages:
            payload = message.payload
            if not isinstance(payload, (bytes, bytearray)):
                continue
            data = bytes(payload)
            topic = str(message.topic)
            if topic.startswith(f"{PREFIX}/user/reply/"):
                self._handle_reply(data)
            elif topic == cmd_topic(self._device_id):
                await self._handle_command(data)

    def _build_pipeline(self) -> VoicePipeline | None:
        try:
            source = build_source(self._voice)
            self._sink = build_sink(self._voice)
            wake = build_wake(self._voice)
            recognizer = build_recognizer(self._voice)
            self._synth = build_synthesizer(self._voice)
        except ImportError as exc:
            log.error(
                'не хватает зависимостей для голоса (%s). Поставь: pip install -e ".[voice]"',
                exc,
            )
            return None
        except (RuntimeError, ValueError) as exc:
            log.error("ошибка конфигурации голоса: %s", exc)
            return None
        return VoicePipeline(
            source=source,
            wake=wake,
            recognizer=recognizer,
            synthesizer=self._synth,
            sink=self._sink,
            settings=self._voice,
            on_transcript=self._on_transcript,
            on_event=self._on_event,
        )

    async def run(self) -> None:
        offline = _build_manifest(self._device_id, online=False)
        will = aiomqtt.Will(
            topic=registry_topic(self._device_id),
            payload=offline.model_dump_json().encode(),
            qos=1,
            retain=True,
        )
        log.info(
            "Голосовой агент '%s' стартует (wake=%s, stt=%s, tts=%s, audio=%s)",
            self._device_id,
            self._voice.wake,
            self._voice.stt,
            self._voice.tts,
            self._voice.audio,
        )

        async with Bus(self._bus_settings, client_id=self._device_id, will=will) as bus:
            self._bus = bus
            pipeline = self._build_pipeline()
            if pipeline is None:
                return

            online = _build_manifest(self._device_id, online=True)
            await bus.publish_model(registry_topic(self._device_id), online, retain=True)
            await bus.subscribe(USER_REPLY_WILDCARD)
            await bus.subscribe(cmd_topic(self._device_id))
            log.info("Голосовой агент подключён, слушаю reply + команды say")

            try:
                await asyncio.gather(self._consume(), pipeline.run())
            finally:
                with contextlib.suppress(Exception):
                    await bus.publish_model(
                        registry_topic(self._device_id),
                        _build_manifest(self._device_id, online=False),
                        retain=True,
                    )


async def run() -> None:
    setup_logging()
    await VoiceApp(BusSettings(), VoiceSettings()).run()


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
