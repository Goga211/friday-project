# Christopher

Распределённый AI-ассистент уровня Jarvis: управление компьютером + умный дом.
Кросс-платформенная система с **облачным мозгом** (Claude API) и лёгким always-on **Hub'ом**.

> Полный план — в Obsidian (`Проекты/Christopher — мастер-план.md`). Здесь — код.

## Архитектура (кратко)

- **Облако** — мозг (Claude API) и распознавание речи (STT).
- **Hub** (мини-ПК/Pi, 24/7) — брокер MQTT, wake-word, оркестрация, вызовы облака, планировщик, TTS.
- **Агенты-исполнители** на устройствах (ПК/ноут/телефон) — выполняют команды, объявляют возможности.
- Связь — **MQTT** (Mosquitto). Контракт сообщений — `src/christopher/shared/protocol.py`.

## Статус: Phase 2 (в работе) — голос

Готово:
- **Phase 0** — монорепо, протокол, шина, реестр устройств, ping/pong, Mosquitto в Docker.
- **Phase 1 (срез 1)** — мозг (Claude tool-use) в Core: получает запрос → Claude выбирает
  инструменты (возможности агента) → Core гоняет команды на агента → возвращает ответ.
  Текстовый CLI, аудит действий (SQLite), лимит шагов агентного цикла. Навыки агента (safe):
  ping, system_info, notify. Кросс-платформенный агент; реальные навыки управления (launch_app,
  run_command с allowlist, окна Win32/UIA) — следующий срез.
- **Phase 2 (срез 1)** — голосовой агент на Hub'е: пайплайн wake-word → запись фразы →
  облачный STT → мозг → TTS. Всё за swappable-интерфейсами (`SpeechProvider`/`WakeWord`/
  `TTS`/аудио-I/O), реальные адаптеры — **openWakeWord**, **Yandex SpeechKit**, **Piper**,
  **sounddevice**; фейки для тестов (пайплайн проверяется без микрофона и облака). Голос
  переиспользует путь мозга (user/request → user/reply) и объявляет возможность `say`.
  Живой прогон (микрофон + Yandex Cloud + обученная модель «Кристофер») — на стороне пользователя.

## Быстрый старт

Нужно: Python 3.12+, Docker. Если нет pip/venv: `sudo apt install python3-venv python3-pip`.

```bash
# 1. Зависимости в venv
make install

# 2. Ключ Claude (мозг). Без него Core работает, но отвечает «мозг недоступен».
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Поднять брокер MQTT (Mosquitto в Docker)
make broker

# 4. Три терминала:
make core         # Core (Hub) — мозг + реестр
make desktop      # desktop-агент
make cli          # текстовый чат с Кристофером
```

В CLI: `покажи инфо о системе` → Claude вызовет `system_info` на агенте и ответит.
Без `ANTHROPIC_API_KEY` проверяется только обвязка (Core ответит «мозг недоступен»).

### Голос (Phase 2)

```bash
make install-voice            # Python-extra: openwakeword, sounddevice, numpy, httpx
make piper                    # качает бинарь Piper + русский голос в vendor/ и models/
```

Настрой `.env` (см. `.env.example`, блок `CHRISTOPHER_VOICE_`): ключ Yandex SpeechKit
(`YANDEX_API_KEY`/`YANDEX_FOLDER_ID`) и пути Piper (их печатает `make piper`).

**Быстрый живой тест (без обучения wake-word)** — активация по Enter:

```bash
# в .env: CHRISTOPHER_VOICE_WAKE=pushtotalk
make voice        # нужен запущенный брокер + Core (make broker, make core)
```

Жмёшь Enter → говоришь фразу → пауза → слышишь ответ. Так проверяется весь боевой путь
(микрофон → STT → мозг → TTS) до того, как обучена модель.

**Полноценный wake-word** «Кристофер» (`CHRISTOPHER_VOICE_WAKE=openwakeword` + `WAKE_MODEL`)
обучается отдельно — русское слово нужно тренировать под свой голос:

```bash
scripts/install-piper.sh dmitri && scripts/install-piper.sh irina  # русские голоса
scripts/train-wakeword.sh 1500                                      # позитивы «Кристофер»
# дальше — обучение openWakeWord, см. docs/wake-word-training.md
```

Полный разбор (почему не англоязычный генератор, обучение, тюнинг порога) —
[docs/wake-word-training.md](docs/wake-word-training.md).

## Разработка

```bash
make test        # pytest
make lint        # ruff
make typecheck   # mypy
make fmt         # black + ruff --fix
```

## Структура

```
src/christopher/
  shared/     # протокол, топики, конфиг, шина MQTT, логирование
  core/       # Core/Hub: реестр устройств, оркестрация (мозг — в Phase 1)
  agents/
    desktop/  # тонкий агент-исполнитель (Linux+Windows)
    voice/    # голосовой агент на Hub'е: wake→STT→мозг→TTS (за swappable-интерфейсами)
infra/        # Mosquitto (docker-compose), конфиг, gen-certs (mTLS)
tests/        # юнит-тесты протокола/топиков/диспатча
docs/adr/     # архитектурные решения
```
