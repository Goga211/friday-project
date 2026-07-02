#!/usr/bin/env python3
"""Генерация позитивных сэмплов wake-word «Пятница» для обучения openWakeWord.

Два движка синтеза:
  --engine piper  — офлайн, бесплатно, 4 русских голоса Piper (install-piper.sh) + вариация
                    темпа/шумности. Роботно, но валидные фонетические паттерны.
  --engine yandex — Yandex SpeechKit v3 (15 живых голосов + вариация темпа): куда больше
                    разнообразия дикторов → модель лучше обобщает. Требует ключ (как у STT/TTS),
                    сеть и стоит копейки за фразу.

Дальнейшую аугментацию (реверберация, фоновый шум, негативы) делает уже сам тренинг
openWakeWord — ему нужны чистые позитивы. Все клипы приводятся к 16 кГц / 16-бит / моно.

Примеры:
  scripts/gen-wakeword-samples.py --engine yandex --count 600
  scripts/gen-wakeword-samples.py --engine piper  --count 1000
"""

from __future__ import annotations

import argparse
import audioop  # stdlib-ресемплер: без внешних бинарей (ffmpeg/sox не нужны)
import random
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHRASE = "Пятница"
TARGET_RATE = 16000  # требование openWakeWord

# Piper: вариации темпа и «шумности».
LENGTH_SCALES = (0.85, 1.0, 1.15, 1.3)
NOISE_SCALES = (0.4, 0.667, 0.9)
NOISE_WIDTHS = (0.6, 0.8)

# Yandex v3: голоса (жен + муж) и вариации темпа.
YANDEX_VOICES = (
    "alena", "jane", "omazh", "dasha", "julia", "lera", "masha", "marina",
    "filipp", "ermil", "zahar", "madirus", "alexander", "kirill", "anton",
)
YANDEX_SPEEDS = (0.9, 1.0, 1.1, 1.2)


def _write_wav_16k_mono(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(pcm)


# --- Piper backend ---
def _find_piper_voices(models_dir: Path) -> list[Path]:
    return sorted(models_dir.glob("ru_RU-*.onnx"))


def _piper_pcm(
    piper_bin: str, voice: Path, phrase: str, tmp_wav: Path, rng: random.Random
) -> bytes:
    cmd = [
        piper_bin, "--model", str(voice), "--output_file", str(tmp_wav),
        "--length_scale", str(rng.choice(LENGTH_SCALES)),
        "--noise_scale", str(rng.choice(NOISE_SCALES)),
        "--noise_w", str(rng.choice(NOISE_WIDTHS)),
    ]
    subprocess.run(cmd, input=phrase.encode(), check=True, capture_output=True)  # noqa: S603
    with wave.open(str(tmp_wav), "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        pcm = w.readframes(w.getnframes())
    if channels > 1:
        pcm = audioop.tomono(pcm, width, 0.5, 0.5)
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
        width = 2
    if rate != TARGET_RATE:
        pcm, _ = audioop.ratecv(pcm, width, 1, rate, TARGET_RATE, None)
    return pcm


def _gen_piper(phrase: str, out_dir: Path, count: int, models_dir: Path, piper_bin: str,
               rng: random.Random) -> int:
    voices = _find_piper_voices(models_dir)
    if not voices:
        print(f"Нет голосов Piper в {models_dir} (см. install-piper.sh)", file=sys.stderr)
        return 0
    if not Path(piper_bin).exists():
        print(f"Не найден Piper: {piper_bin} (см. install-piper.sh)", file=sys.stderr)
        return 0
    print(f"Piper-голоса ({len(voices)}): {', '.join(v.stem for v in voices)}")
    tmp = out_dir / "_tmp.wav"
    made = 0
    for i in range(count):
        voice = voices[i % len(voices)]
        try:
            pcm = _piper_pcm(piper_bin, voice, phrase, tmp, rng)
            _write_wav_16k_mono(out_dir / f"friday_{i:05d}.wav", pcm)
            made += 1
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.decode(errors="replace") if exc.stderr else ""
            print(f"✗ сэмпл {i} ({voice.name}): {err.strip()[:100]}", file=sys.stderr)
        if made and made % 100 == 0:
            print(f"  … {made}/{count}")
    tmp.unlink(missing_ok=True)
    return made


# --- Yandex backend ---
def _gen_yandex(phrase: str, out_dir: Path, count: int, rng: random.Random) -> int:
    try:
        from speechkit import configure_credentials, creds, model_repository

        from friday.agents.voice.config import VoiceSettings
    except ImportError as exc:
        print(f"Нет зависимостей Yandex TTS: {exc} (pip install -e '.[voice]')", file=sys.stderr)
        return 0
    settings = VoiceSettings()
    if not settings.yandex_api_key:
        print("FRIDAY_VOICE_YANDEX_API_KEY не задан (.env)", file=sys.stderr)
        return 0
    configure_credentials(
        yandex_credentials=creds.YandexCredentials(api_key=settings.yandex_api_key)
    )
    print(f"Yandex-голоса ({len(YANDEX_VOICES)}): {', '.join(YANDEX_VOICES)}")
    made = 0
    for i in range(count):
        voice = YANDEX_VOICES[i % len(YANDEX_VOICES)]
        try:
            model = model_repository.synthesis_model()
            model.voice = voice
            model.speed = rng.choice(YANDEX_SPEEDS)
            seg = model.synthesize(phrase, raw_format=False)
            seg = seg.set_frame_rate(TARGET_RATE).set_channels(1).set_sample_width(2)
            _write_wav_16k_mono(out_dir / f"friday_{i:05d}.wav", seg.raw_data)
            made += 1
        except Exception as exc:  # noqa: BLE001 — один сбойный голос не должен ронять генерацию
            print(f"✗ сэмпл {i} ({voice}): {str(exc)[:100]}", file=sys.stderr)
        if made and made % 100 == 0:
            print(f"  … {made}/{count}")
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phrase", default=DEFAULT_PHRASE)
    parser.add_argument("--engine", choices=["piper", "yandex"], default="piper")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data" / "wake" / "positive")
    parser.add_argument("--models-dir", type=Path, default=ROOT / "models")
    parser.add_argument("--piper-bin", default=str(ROOT / "vendor" / "piper" / "piper"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    print(f"Движок: {args.engine}. Генерирую {args.count} сэмплов «{args.phrase}» → {args.out_dir}")
    if args.engine == "piper":
        made = _gen_piper(
            args.phrase, args.out_dir, args.count, args.models_dir, args.piper_bin, rng
        )
    else:
        made = _gen_yandex(args.phrase, args.out_dir, args.count, rng)

    print(f"✓ готово: {made}/{args.count} сэмплов в {args.out_dir}")
    print("Дальше — обучение модели: docs/wake-word-training.md")
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
