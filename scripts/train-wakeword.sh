#!/usr/bin/env bash
# Готовит данные для обучения wake-word «Кристофер»: проверяет Piper и русские голоса,
# генерирует позитивные сэмплы и печатает следующие шаги обучения openWakeWord.
# Само обучение (torch, GPU, гигабайты негативов) выносится в отдельное окружение —
# см. docs/wake-word-training.md. Здесь — детерминированная часть: подготовка позитивов.
#
# Использование:
#   scripts/train-wakeword.sh [count]
# count: сколько позитивных сэмплов сгенерировать (по умолчанию 1500).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COUNT="${1:-1500}"
PHRASE="Кристофер"
POSITIVE_DIR="${ROOT}/data/wake/positive"
PIPER_BIN="${ROOT}/vendor/piper/piper"
MODELS="${ROOT}/models"

# --- Проверки окружения ---
if [[ ! -x "${PIPER_BIN}" ]]; then
  echo "✗ Piper не установлен (${PIPER_BIN})."
  echo "  Поставь: scripts/install-piper.sh dmitri"
  exit 1
fi

shopt -s nullglob
VOICES=("${MODELS}"/ru_RU-*.onnx)
shopt -u nullglob
if [[ ${#VOICES[@]} -eq 0 ]]; then
  echo "✗ Нет русских голосов Piper в ${MODELS}."
  echo "  Поставь хотя бы пару для разнообразия дикторов:"
  echo "    scripts/install-piper.sh dmitri && scripts/install-piper.sh irina"
  exit 1
fi
echo "✓ Голоса (${#VOICES[@]}): $(for v in "${VOICES[@]}"; do basename "$v" .onnx; done | tr '\n' ' ')"

if [[ ${#VOICES[@]} -lt 2 ]]; then
  echo "⚠ Установлен только один голос — датасет будет однообразным."
  echo "  Рекомендуется 3–4 голоса (dmitri/irina/denis/ruslan)."
fi

# --- Генерация позитивов ---
GEN="${ROOT}/scripts/gen-wakeword-samples.py"
PYTHON="$(command -v python3 || command -v python)"
echo "→ Генерирую ${COUNT} позитивных сэмплов «${PHRASE}»…"
"${PYTHON}" "${GEN}" --phrase "${PHRASE}" --count "${COUNT}" --out-dir "${POSITIVE_DIR}"

COUNT_MADE=$(find "${POSITIVE_DIR}" -name 'christopher_*.wav' | wc -l | tr -d ' ')

cat <<EOF

────────────────────────────────────────────────────────────────────
Позитивы готовы: ${COUNT_MADE} файлов в ${POSITIVE_DIR#"${ROOT}/"}

Совет: домешай 20–50 своих ЖИВЫХ записей «Кристофер» в эту же папку
(разные комнаты, расстояние, громкость) — заметно поднимает точность.

Следующий шаг — обучение (отдельное окружение, GPU желателен):

  python3 -m venv .venv-train && source .venv-train/bin/activate
  pip install "openwakeword[full]"
  git clone https://github.com/dscripka/openWakeWord

Затем в notebooks/automatic_model_training.ipynb укажи наши позитивы
(${POSITIVE_DIR#"${ROOT}/"}) вместо англоязычного генератора и обучи
модель christopher.onnx. Полный гайд: docs/wake-word-training.md

После обучения:
  cp .../christopher.onnx models/
  echo 'CHRISTOPHER_VOICE_WAKE_MODEL=models/christopher.onnx' >> .env
────────────────────────────────────────────────────────────────────
EOF
