#!/usr/bin/env bash
# Ставит Piper (TTS) локально: бинарь + русский голос. Ничего не требует от системы,
# кроме curl и tar. Артефакты кладёт в vendor/ и models/ (оба в .gitignore).
#
# Использование:
#   scripts/install-piper.sh [voice]
# voice: dmitri (по умолч.) | irina | denis | ruslan — русские голоса piper-voices (medium).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOICE="${1:-dmitri}"

PIPER_TAG="2023.11.14-2"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/${PIPER_TAG}/piper_linux_x86_64.tar.gz"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/${VOICE}/medium"
VOICE_NAME="ru_RU-${VOICE}-medium"

VENDOR="${ROOT}/vendor"
MODELS="${ROOT}/models"
PIPER_BIN="${VENDOR}/piper/piper"
VOICE_ONNX="${MODELS}/${VOICE_NAME}.onnx"

mkdir -p "${VENDOR}" "${MODELS}"

# --- Piper бинарь ---
if [[ -x "${PIPER_BIN}" ]]; then
  echo "✓ Piper уже установлен: ${PIPER_BIN}"
else
  echo "→ Скачиваю Piper ${PIPER_TAG}…"
  curl -fL "${PIPER_URL}" -o "${VENDOR}/piper.tar.gz"
  tar -xzf "${VENDOR}/piper.tar.gz" -C "${VENDOR}"
  rm -f "${VENDOR}/piper.tar.gz"
  echo "✓ Piper: ${PIPER_BIN}"
fi

# --- Русский голос ---
if [[ -f "${VOICE_ONNX}" && -f "${VOICE_ONNX}.json" ]]; then
  echo "✓ Голос уже скачан: ${VOICE_ONNX}"
else
  echo "→ Скачиваю голос ${VOICE_NAME}…"
  curl -fL "${VOICE_BASE}/${VOICE_NAME}.onnx" -o "${VOICE_ONNX}"
  curl -fL "${VOICE_BASE}/${VOICE_NAME}.onnx.json" -o "${VOICE_ONNX}.json"
  echo "✓ Голос: ${VOICE_ONNX}"
fi

cat <<EOF

Готово. Пропиши в .env (пути относительно корня репозитория):

  CHRISTOPHER_VOICE_PIPER_BIN=${PIPER_BIN#"${ROOT}/"}
  CHRISTOPHER_VOICE_PIPER_MODEL=${VOICE_ONNX#"${ROOT}/"}
  CHRISTOPHER_VOICE_PIPER_SAMPLE_RATE=22050

Проверка синтеза (должен получиться WAV):
  echo "Привет, я Кристофер" | ${PIPER_BIN} --model ${VOICE_ONNX} --output_file /tmp/test.wav
EOF
