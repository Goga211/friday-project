# iPhone как пульт Пятницы: Siri Shortcuts + push

Телефон не слушает wake-word (Apple не даёт фоновый микрофон), поэтому iPhone — «пульт»:
команда уходит через Shortcuts в REST API HUD'а, ответы и напоминания прилетают push'ем
через ntfy. Всегда через Hub (вне дома — по Tailscale), напрямую к ПК телефон не ходит.

## 1. Сеть: Tailscale до Hub'а

1. Поставь [Tailscale](https://tailscale.com) на Hub и на iPhone, войди в один tailnet.
2. Узнай tailscale-адрес Hub'а: `tailscale ip -4` (вида `100.x.y.z`).
3. HUD должен слушать не только localhost: в `.env` на Hub'е задай
   `FRIDAY_HUD_HOST=0.0.0.0` (или конкретный tailscale-IP).

## 2. Токен API

REST API выключен, пока не задан токен:

```bash
# .env на Hub'е
FRIDAY_HUD_TOKEN=$(openssl rand -hex 24)   # или любой длинный секрет
FRIDAY_HUD_HOST=0.0.0.0
```

Перезапусти HUD (`make hud`). Проверка с любой машины tailnet'а:

```bash
curl -s -X POST http://100.x.y.z:8010/api/message \
  -H "Authorization: Bearer <токен>" \
  -H "Content-Type: application/json" \
  -d '{"text": "привет"}'
# → {"text": "...", "reply_id": "...", "pending": []}
```

- `401` — неверный токен; `503` — токен не задан или шина недоступна; `504` — мозг молчит.
- Если ответ содержит `pending` (risky-действие), подтверждение:
  `POST /api/confirm` с `{"reply_id": "<из ответа>", "approved": true}`.

## 3. Shortcut на iPhone

Создай Shortcut «Пятница»:

1. **Продиктовать текст** (Dictate Text, язык — русский) — или «Спросить» для ввода руками.
2. **Get Contents of URL**:
   - URL: `http://100.x.y.z:8010/api/message`
   - Method: `POST`, Request Body: `JSON`, поле `text` = продиктованный текст;
   - Headers: `Authorization` = `Bearer <токен>`.
3. **Get Dictionary Value** → ключ `text`.
4. **Показать результат** (или «Произнести текст» — Siri озвучит ответ).

Запуск: «Привет Siri, Пятница» / кнопка Action Button / иконка на экране.

## 4. Push-уведомления (ntfy)

Пятница шлёт push'и на телефон (в т.ч. по расписанию, когда ПК выключен) через
[ntfy](https://ntfy.sh):

1. Поставь приложение **ntfy** из App Store.
2. Придумай НЕугадываемый топик (он же пароль): например `friday-a8f3k2...`
   (`openssl rand -hex 8` в помощь). Подпишись на него в приложении.
3. На Hub'е в `.env`: `FRIDAY_PUSH_URL=https://ntfy.sh/friday-a8f3k2...`
4. Перезапусти Core — у мозга появится инструмент `notify_phone`.

Проверка: «Пятница, отправь на телефон тест» или
`curl -d '{"topic":"friday-a8f3k2...","message":"тест"}' https://ntfy.sh`.

⚠️ Топик ntfy.sh — публичный сервис: кто знает имя топика, тот читает сообщения.
Длинное случайное имя обязательно; для параноидального режима — свой ntfy-сервер
в tailnet'е.

## Ограничения (осознанные, Phase 3)

- Без собственного приложения нет непрерывного диалога — каждый вызов Shortcut отдельный
  (но контекст помнит Core, так что «а теперь закрой его» работает).
- HTTP внутри tailnet'а (трафик и так шифруется WireGuard'ом); наружу без Tailscale не
  выставлять.
- Swift-приложение (стриминг голоса, история чата) — Phase 5.
