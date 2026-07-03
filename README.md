# Пятница

Распределённый AI-ассистент уровня Jarvis: управление компьютером + умный дом.
Кросс-платформенная система с **облачным мозгом** (Claude API) и лёгким always-on **Hub'ом**.

> Полный план — в Obsidian (`Проекты/Пятница — мастер-план.md`). Здесь — код.

## Архитектура (кратко)

- **Облако** — мозг (Claude API) и распознавание речи (STT).
- **Hub** (мини-ПК/Pi, 24/7) — брокер MQTT, wake-word, оркестрация, вызовы облака, планировщик, TTS.
- **Агенты-исполнители** на устройствах (ПК/ноут/телефон) — выполняют команды, объявляют возможности.
- Связь — **MQTT** (Mosquitto). Контракт сообщений — `src/friday/shared/protocol.py`.

## Статус: Phase 4 — умный дом

Готово:
- **Phase 0** — монорепо, протокол, шина, реестр устройств, ping/pong, Mosquitto в Docker (mTLS).
- **Phase 1** — мозг (Claude tool-use) в Core: запрос → Claude выбирает инструменты
  (возможности агентов) → Core гоняет команды по шине → ответ. CLI и веб-чат (HUD), аудит и
  память диалога (SQLite), scheduler (отложенные/cron-действия), флоу подтверждения risky-действий.
  Кросс-платформенные навыки агента: launch_app, run_command (allowlist), screenshot, open_url,
  type_text, окна (Win32/wmctrl), notify.
- **Phase 2** — голосовой агент на Hub'е: wake-word → запись → облачный STT → мозг → TTS.
  Реальные адаптеры — **openWakeWord**, **Yandex SpeechKit** (STT+TTS v3), **Piper**,
  **sounddevice**; фейки для тестов; barge-in, голосовое подтверждение. Обучение wake-word
  «Пятница» — docs/wake-word-training.md.
- **Phase 3** — мультиустройство: маршрутизация команд по цели («открой на ноутбуке» —
  алиасы устройств, параметр device, list_devices), персистентный реестр устройств,
  power management (wake_device по Wake-on-LAN, sleep/shutdown/reboot, lock_screen),
  iPhone-пульт (REST API HUD для Siri Shortcuts + push через ntfy) — docs/iphone-shortcuts.md.
- **Phase 4** — умный дом: агент `friday-home` с интерфейсом `DeviceController`
  (home_list / home_get_state / home_set_state / home_run_scene). По умолчанию —
  `MockController` (виртуальные лампы/розетка/сцена, железа не нужно); адаптер
  **Home Assistant** готов (`FRIDAY_HOME_CONTROLLER=ha` + URL/token) — ADR 0003.

## Быстрый старт

Нужно: Python 3.12+, Docker. Если нет pip/venv: `sudo apt install python3-venv python3-pip`.

```bash
# 1. Зависимости в venv
make install

# 2. Ключ Claude (мозг). Без него Core работает, но отвечает «мозг недоступен».
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Поднять брокер MQTT (Mosquitto в Docker)
make broker

# 4. Терминалы:
make core         # Core (Hub) — мозг + реестр
make desktop      # desktop-агент
make cli          # текстовый чат с Пятницей
make hud          # веб-чат: http://127.0.0.1:8010 (+ REST API для iPhone)
make home         # агент умного дома (mock; с HA: FRIDAY_HOME_CONTROLLER=ha)
```

Умный дом без железа: `make home` → «включи свет в спальне», «включи сцену вечер»,
«что со светом?» — mock-лампы честно меняют состояние.

Несколько устройств: на каждом — свой агент с `FRIDAY_DEVICE_ID` и `FRIDAY_DEVICE_ALIAS`
(«пк», «ноутбук») в `.env`; дальше «покажи уведомление на ноутбуке» просто работает,
а выключенный ПК будится фразой «разбуди пк» (Wake-on-LAN; вкл. в BIOS).
iPhone как пульт (Siri Shortcuts + push) — [docs/iphone-shortcuts.md](docs/iphone-shortcuts.md).

В CLI: `покажи инфо о системе` → Claude вызовет `system_info` на агенте и ответит.
Без `ANTHROPIC_API_KEY` проверяется только обвязка (Core ответит «мозг недоступен»).

### Голос (Phase 2)

```bash
make install-voice            # Python-extra: openwakeword, sounddevice, numpy, httpx
make piper                    # качает бинарь Piper + русский голос в vendor/ и models/
```

Настрой `.env` (см. `.env.example`, блок `FRIDAY_VOICE_`): ключ Yandex SpeechKit
(`YANDEX_API_KEY`/`YANDEX_FOLDER_ID`) и пути Piper (их печатает `make piper`).

**Быстрый живой тест (без обучения wake-word)** — активация по Enter:

```bash
# в .env: FRIDAY_VOICE_WAKE=pushtotalk
make voice        # нужен запущенный брокер + Core (make broker, make core)
```

Жмёшь Enter → говоришь фразу → пауза → слышишь ответ. Так проверяется весь боевой путь
(микрофон → STT → мозг → TTS) до того, как обучена модель.

**Полноценный wake-word** «Пятница» (`FRIDAY_VOICE_WAKE=openwakeword` + `WAKE_MODEL`)
обучается отдельно — русское слово нужно тренировать под свой голос:

```bash
scripts/install-piper.sh dmitri && scripts/install-piper.sh irina  # русские голоса
scripts/train-wakeword.sh 1500                                      # позитивы «Пятница»
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
src/friday/
  shared/     # протокол, топики, конфиг, шина MQTT, WoL, логирование
  core/       # Core/Hub: мозг, роутер инструментов, реестр, scheduler, аудит, push
  hud/        # веб-чат + REST API (iPhone Shortcuts)
  agents/
    desktop/  # тонкий агент-исполнитель (Linux+Windows): навыки + питание
    voice/    # голосовой агент на Hub'е: wake→STT→мозг→TTS (за swappable-интерфейсами)
    home/     # умный дом: DeviceController (mock | Home Assistant), сцены
infra/        # Mosquitto (docker-compose), конфиг, gen-certs (mTLS)
tests/        # юнит-тесты (все на фейках, без сети/железа)
docs/adr/     # архитектурные решения
```
