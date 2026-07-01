"""Голосовой агент (Phase 2): wake-word → запись → облачный STT → мозг → TTS.

Живёт на Hub'е. Всё аудио-железо и облачные сервисы спрятаны за swappable-интерфейсами
(`interfaces.py`), поэтому пайплайн (`pipeline.py`) тестируется на фейках без микрофона и
без облака. Реальные адаптеры (openWakeWord, Yandex SpeechKit, Piper, sounddevice) —
в `providers/`, выбираются фабрикой (`factory.py`) по настройкам.
"""
