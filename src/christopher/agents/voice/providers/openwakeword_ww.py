"""Wake-word через openWakeWord (локально, единственный ИИ на Hub'е).

Ожидает кадры 16 кГц/16-бит PCM моно кратно 80 мс (см. VoiceSettings.frame_samples).
Модель «Кристофер» обучается отдельно (русское слово нужно тренировать); путь к ней —
CHRISTOPHER_VOICE_WAKE_MODEL. Без модели грузятся предобученные (это НЕ «Кристофер»,
только для дымового теста обвязки).
"""

from __future__ import annotations

import logging

from christopher.agents.voice.config import VoiceSettings

log = logging.getLogger("christopher.voice.wake")


class OpenWakeWordDetector:
    def __init__(self, settings: VoiceSettings) -> None:
        import numpy as np
        from openwakeword.model import Model

        self._np = np
        kwargs: dict[str, object] = {}
        if settings.wake_model:
            kwargs["wakeword_models"] = [settings.wake_model]
            # openWakeWord по умолчанию tflite — для .onnx-модели фреймворк нужно указать явно.
            kwargs["inference_framework"] = (
                "onnx" if settings.wake_model.endswith(".onnx") else "tflite"
            )
        else:
            log.warning(
                "CHRISTOPHER_VOICE_WAKE_MODEL не задан — грузятся предобученные модели "
                "(НЕ «Кристофер»). Обучи свою модель и укажи путь."
            )
        self._model = Model(**kwargs)

    def process(self, frame: bytes) -> float:
        samples = self._np.frombuffer(frame, dtype=self._np.int16)
        scores = self._model.predict(samples)
        if not scores:
            return 0.0
        return float(max(scores.values()))

    def reset(self) -> None:
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            reset()
