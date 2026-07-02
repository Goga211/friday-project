# Обучение wake-word «Пятница»

Локальный детектор пробуждения — единственный ИИ, который крутится на Hub'е постоянно
(см. `openwakeword_ww.py`). Предобученные модели openWakeWord — англоязычные и слова
«Пятница» не знают, поэтому свою модель нужно **обучить**.

GPU локально нет → тренинг делаем на **Google Colab** (бесплатный GPU, ~30 мин). Здесь, в
репозитории, готовим **датасет**; Colab только обучает.

## TL;DR

```bash
# 1. Синтетические позитивы (15 живых голосов Yandex) — нужны ключи Yandex в .env
scripts/gen-wakeword-samples.py --engine yandex --count 750

# 2. Свои ЖИВЫЕ записи «Пятница» (решающие данные) — в тот же датасет
scripts/record-wakeword.py --count 40

# 3. Упаковать для Colab
cd data/wake && zip -r friday_positives.zip positive/ && cd ../..

# 4. Обучить на Colab (см. ниже) → friday.onnx
# 5. Подключить
cp friday.onnx models/ && sed -i 's/FRIDAY_VOICE_WAKE=.*/FRIDAY_VOICE_WAKE=openwakeword/' .env
echo 'FRIDAY_VOICE_WAKE_MODEL=models/friday.onnx' >> .env
```

## Шаг 1. Синтетические позитивы

`scripts/gen-wakeword-samples.py` — два движка:

- `--engine yandex` (рекомендуется): 15 нейроголосов Yandex v3 + вариация темпа. Разнообразные
  живые дикторы → модель лучше обобщает. Нужны ключи Yandex в `.env` (те же, что STT/TTS),
  сеть, копейки за генерацию.
- `--engine piper`: офлайн, бесплатно, 4 голоса Piper (роботно, но фонетика валидна).

Все клипы приводятся к **16 кГц / 16-бит / моно** (требование openWakeWord). Лежат в
`data/wake/positive/` (gitignored).

```bash
scripts/gen-wakeword-samples.py --engine yandex --count 750
```

## Шаг 2. Свои живые записи (важнее всего)

Синтетика даёт обобщение, но wake-word должен надёжно срабатывать на **твой** голос, микрофон
и акустику комнаты — этого синтетика не покрывает. Запиши 30–50 своих «Пятница»:

```bash
scripts/record-wakeword.py --count 40
```

По Enter пишет ~1.5 с в тот же датасет (префикс `friday_live_`). Меняй интонацию, громкость,
расстояние до микрофона, комнату — чем разнообразнее, тем устойчивее модель.

## Шаг 3. Упаковка

```bash
cd data/wake && zip -r friday_positives.zip positive/ && cd ../..
```

Получишь `data/wake/friday_positives.zip` — его зальёшь в Colab.

## Шаг 4. Обучение на Colab

Каноничный путь — ноутбук openWakeWord с бесплатным GPU; наши позитивы подставляются вместо
англоязычного генератора.

1. Открой **`automatic_model_training.ipynb`** из репозитория openWakeWord в Colab:
   `https://github.com/dscripka/openWakeWord` → `notebooks/automatic_model_training.ipynb` →
   кнопка «Open in Colab».
2. **Runtime → Change runtime type → GPU** (T4 бесплатно).
3. **Залей `friday_positives.zip`** (панель Files слева) и распакуй в ячейке:
   ```python
   !unzip -q friday_positives.zip -d my_positives
   ```
4. В конфиге тренинга (ячейка с YAML/параметрами):
   - `target_word: "Пятница"` (метка), `model_name: friday`,
   - **путь к позитивам** → `my_positives/positive` (вместо генерации `piper-sample-generator`;
     ячейку с генерацией англоязычных позитивов пропусти/закомментируй),
   - негативы/фоновый шум/импульсы — оставь как в ноутбуке (скачиваются автоматически),
   - `steps: 10000` для старта (можно больше при слабом качестве).
5. **Runtime → Run all**. Через ~20–40 мин появится `friday.onnx` (openWakeWord умеет и `.tflite`;
   мы грузим `.onnx`).
6. Скачай `friday.onnx`.

## Шаг 5. Подключение и тюнинг

```bash
cp ~/Downloads/friday.onnx models/
# .env: включить openwakeword и указать модель
sed -i 's/FRIDAY_VOICE_WAKE=.*/FRIDAY_VOICE_WAKE=openwakeword/' .env
grep -q FRIDAY_VOICE_WAKE_MODEL .env || echo 'FRIDAY_VOICE_WAKE_MODEL=models/friday.onnx' >> .env
make voice   # теперь hands-free: скажи «Пятница»
```

| Симптом | Что крутить |
|---|---|
| Не реагирует на «Пятница» | ↓ `FRIDAY_VOICE_WAKE_THRESHOLD` (напр. 0.3); больше живых записей |
| Ложные срабатывания | ↑ порог (0.6–0.7); больше шагов обучения |
| Перебивает сам себя во время ответа | ↑ порог или `FRIDAY_VOICE_BARGE_IN=false` |

## Файлы

- `scripts/gen-wakeword-samples.py` — синтетические позитивы (yandex|piper).
- `scripts/record-wakeword.py` — живые записи своего голоса.
- `src/friday/agents/voice/providers/openwakeword_ww.py` — загрузка обученной модели.
