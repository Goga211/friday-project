# Обучение wake-word «Пятница»

Локальный детектор пробуждения — единственный ИИ, который крутится на Hub'е постоянно
(см. `openwakeword_ww.py`). Предобученные модели openWakeWord — англоязычные и слова
«Пятница» не знают, поэтому свою модель нужно **обучить**. Ниже — практичный путь под
русское слово.

## TL;DR

```bash
# 1. Поставить Piper и несколько русских голосов (для разнообразия дикторов)
scripts/install-piper.sh dmitri
scripts/install-piper.sh irina
scripts/install-piper.sh denis
scripts/install-piper.sh ruslan

# 2. Сгенерировать позитивные сэмплы «Пятница»
scripts/gen-wakeword-samples.py --count 1500 --out-dir data/wake/positive

# 3. Обучить модель openWakeWord (см. раздел «Обучение») → models/friday.onnx

# 4. Прописать в .env и проверить
echo 'FRIDAY_VOICE_WAKE_MODEL=models/friday.onnx' >> .env
```

## Почему не «автоматический» генератор openWakeWord

Официальный `automatic_model_training.ipynb` генерирует позитивы через
`piper-sample-generator` — а это **английская** модель LibriTTS. Слово «Пятница»
кириллицей она произнесёт неправильно, и модель обучится не на том звуке.

Решение: генерируем позитивы уже установленным **русским** Piper, прогоняя фразу через
несколько голосов с вариацией темпа и «шумности» (`gen-wakeword-samples.py`). Это даёт
акустическое разнообразие (разные дикторы, скорость, интонация). Остальную аугментацию —
реверберацию, фоновый шум, подбор негативов — делает уже сам тренинг openWakeWord.

> **Лучший результат** — домешать 20–50 своих живых записей «Пятница» (разные комнаты,
> расстояние до микрофона, громкость) в ту же папку `data/wake/positive`. Синтез хорошо
> обобщает по дикторам, но твой реальный голос/микрофон/акустика синтетика не покрывает.

## Шаг 1–2. Позитивные сэмплы

`scripts/gen-wakeword-samples.py`:

- берёт русские голоса из `models/ru_RU-*.onnx` (их ставит `install-piper.sh`),
- чередует голоса и случайно варьирует `length_scale` (темп) и `noise_scale/noise_w`,
- приводит каждый клип к **16 кГц / 16-бит / моно** (требование openWakeWord) на stdlib,
  без ffmpeg.

```bash
scripts/gen-wakeword-samples.py \
  --phrase "Пятница" \
  --count 1500 \
  --out-dir data/wake/positive
```

Чем больше голосов установлено — тем разнообразнее датасет. 1000–2000 сэмплов достаточно
для старта.

## Шаг 3. Обучение модели openWakeWord

Обучение тяжёлое (нужен GPU, скачиваются несколько ГБ негативов и импульсных характеристик
комнат). Каноничный путь — ноутбук openWakeWord; наши позитивы подставляются вместо
англоязычных.

```bash
# Отдельное окружение под тренинг (не в проектный .venv — тянет torch и пр.)
python3 -m venv .venv-train && source .venv-train/bin/activate
pip install "openwakeword[full]"

git clone https://github.com/dscripka/openWakeWord
cd openWakeWord
```

Дальше — по `notebooks/automatic_model_training.ipynb` (или `custom_model.ipynb`), с двумя
отличиями под наш случай:

1. **Пропустить генерацию позитивов** англоязычным `piper-sample-generator`. Вместо этого
   указать в конфиге путь к нашим клипам из `data/wake/positive`.
2. В конфиге тренинга задать:
   - `target_phrase: "Пятница"` (метка модели),
   - `model_name: friday`,
   - негативы/валидацию — предвычисленные фичи openWakeWord (ссылки на них есть в ноутбуке;
     скачиваются автоматически),
   - число шагов: начать с `steps: 10000–50000`.

На выходе — `friday.onnx` (openWakeWord умеет экспортировать и `.tflite`; мы грузим
`.onnx`, см. `openwakeword_ww.py`).

> Без GPU обучение возможно, но медленное. Как альтернатива — тот же ноутбук в Google Colab
> (бесплатный GPU): загрузить туда архив `data/wake/positive` и следовать шагам выше.

## Шаг 4. Подключение и проверка

```bash
cp openWakeWord/models/friday.onnx models/     # положить рядом с голосами
echo 'FRIDAY_VOICE_WAKE_MODEL=models/friday.onnx' >> .env
```

Живой тест детектора (без облака и мозга — только срабатывание wake-word):

```bash
# порог по умолчанию 0.5; понижай при пропусках, повышай при ложных срабатываниях
FRIDAY_VOICE_WAKE_THRESHOLD=0.5 python -m friday.agents.voice.app
```

Полный голосовой контур (wake → запись → STT → мозг → TTS → barge-in) — как обычно, при
заданных ключах Yandex STT и установленном Piper.

## Тюнинг

| Симптом | Что крутить |
|---|---|
| Не реагирует на «Пятница» | ↓ `FRIDAY_VOICE_WAKE_THRESHOLD` (напр. 0.3); больше живых позитивов |
| Срабатывает на посторонние слова | ↑ порог (0.6–0.7); больше шагов обучения; больше негативов |
| Реагирует на TTS-ответ (самоперебивание barge-in) | ↑ порог или `FRIDAY_VOICE_BARGE_IN=false` |

## Файлы

- `scripts/install-piper.sh` — Piper + русские голоса.
- `scripts/gen-wakeword-samples.py` — генерация позитивов «Пятница».
- `scripts/train-wakeword.sh` — обёртка: проверки + генерация + подсказки по обучению.
- `src/friday/agents/voice/providers/openwakeword_ww.py` — загрузка обученной модели.
