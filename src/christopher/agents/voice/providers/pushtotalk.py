"""Push-to-talk: активация записи по нажатию Enter (без обученной wake-модели).

Даёт услышать весь боевой конвейер (микрофон → STT → мозг → TTS) до того, как обучена
модель «Кристофер». Реализует тот же интерфейс WakeWordDetector: фоновый поток слушает
stdin, а process() возвращает 1.0 один раз на каждое нажатие Enter — пайплайн начинает
запись фразы и завершает её по тишине (VAD).
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("christopher.voice.ptt")

_PROMPT = "[push-to-talk] нажми Enter и говори…"


class PushToTalkWakeWord:
    def __init__(self) -> None:
        self._pending = threading.Event()
        thread = threading.Thread(target=self._listen, daemon=True)
        thread.start()
        print(_PROMPT, flush=True)

    def _listen(self) -> None:
        while True:
            try:
                input()
            except Exception:  # noqa: BLE001 — закрытый/недоступный stdin просто завершает поток
                return
            self._pending.set()

    def process(self, frame: bytes) -> float:
        if self._pending.is_set():
            self._pending.clear()
            print("[push-to-talk] пишу фразу…", flush=True)
            return 1.0
        return 0.0

    def reset(self) -> None:
        print(_PROMPT, flush=True)
