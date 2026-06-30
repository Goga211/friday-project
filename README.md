# Christopher

Распределённый AI-ассистент уровня Jarvis: управление компьютером + умный дом.
Кросс-платформенная система с **облачным мозгом** (Claude API) и лёгким always-on **Hub'ом**.

> Полный план — в Obsidian (`Проекты/Christopher — мастер-план.md`). Здесь — код.

## Архитектура (кратко)

- **Облако** — мозг (Claude API) и распознавание речи (STT).
- **Hub** (мини-ПК/Pi, 24/7) — брокер MQTT, wake-word, оркестрация, вызовы облака, планировщик, TTS.
- **Агенты-исполнители** на устройствах (ПК/ноут/телефон) — выполняют команды, объявляют возможности.
- Связь — **MQTT** (Mosquitto). Контракт сообщений — `src/christopher/shared/protocol.py`.

## Статус: Phase 0 — фундамент

Готово: монорепо, протокол сообщений, шина (aiomqtt), скелет Core + Linux desktop-агент
(ping/pong, анонс возможностей, реестр устройств), Mosquitto в Docker.

## Быстрый старт

Нужно: Python 3.12+, Docker. Если нет pip/venv: `sudo apt install python3-venv python3-pip`.

```bash
# 1. Зависимости в venv
make install

# 2. Поднять брокер MQTT (Mosquitto в Docker)
make broker

# 3. В одном терминале — Core (Hub)
make core

# 4. В другом — desktop-агент
make desktop
```

Агент при старте объявит возможности → Core увидит его в реестре и начнёт пинговать;
в логах Core пойдут ответы `pong`. Это и есть проверка Phase 0.

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
infra/        # Mosquitto (docker-compose), конфиг, gen-certs (mTLS)
tests/        # юнит-тесты протокола/топиков/диспатча
docs/adr/     # архитектурные решения
```
