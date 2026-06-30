# ADR 0001 — Шина сообщений: MQTT (Mosquitto)

Статус: принято (Phase 0). Дата: 2026-07-01.

## Контекст
Распределённая система: центральный Hub + агенты на устройствах (ПК, ноут, телефон) +
будущие IoT/сателлиты. Нужна связь request/reply (команды→ответы) и pub/sub (события,
анонс возможностей), кросс-платформенная и пригодная для мобильных/IoT.

## Решение
**MQTT** через брокер **Mosquitto**. Один брокер для всего:
- IoT-native, клиенты под все платформы, MQTT-over-WebSocket для мобильных/веба;
- retained-сообщения удобны для анонса возможностей (registry) и статуса online/offline;
- Last-Will для авто-перевода агента в offline при обрыве.

Топики и контракт — `src/christopher/shared/topics.py` и `protocol.py`.
Клиент — `aiomqtt` (async, поверх paho).

## Альтернатива
**NATS** (нативный request-reply, JetStream) — быстрее и удобнее для RPC, но менее
IoT-/mobile-дружелюбен и добавляет инфраструктуры. Начинаем с MQTT (KISS); пересмотрим,
если упрёмся в латентность/надёжность RPC.

## Безопасность
Dev — plaintext на localhost (anonymous). Боевой режим — **mTLS** (listener 8883,
per-device сертификаты, `infra/scripts/gen-certs.sh`) + ACL.
