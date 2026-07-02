#!/usr/bin/env python3
"""Генерация позитивных сэмплов wake-word «Пятница» для обучения openWakeWord.

Русского многоголосого TTS-генератора у openWakeWord нет (их piper-sample-generator —
английская модель и «Пятница» произнесёт неверно). Поэтому синтезируем позитивы уже
установленным русским Piper (scripts/install-piper.sh), гоняя фразу через НЕСКОЛЬКО голосов
с вариацией темпа и «шумности» — так набирается акустическое разнообразие (разные дикторы,
скорость, интонация), нужное модели для обобщения. Дальнейшую аугментацию (реверберация,
фоновый шум, негативы) делает уже сам тренинг openWakeWord — ему нужны чистые позитивы.

Ресемплинг 22050→16000 Гц и приведение к моно — на stdlib (wave + audioop), без ffmpeg.

Пример:
  scripts/gen-wakeword-samples.py --count 1500 --out-dir data/wake/positive
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

# Матрица вариаций озвучки (Piper): темп (length_scale) и «шумность» голоса.
LENGTH_SCALES = (0.85, 1.0, 1.15, 1.3)
NOISE_SCALES = (0.4, 0.667, 0.9)
NOISE_WIDTHS = (0.6, 0.8)


def _find_voices(models_dir: Path) -> list[Path]:
    """Русские голоса Piper из models/ (их ставит install-piper.sh)."""
    return sorted(models_dir.glob("ru_RU-*.onnx"))


def _synthesize(
    piper_bin: str, voice: Path, phrase: str, out_wav: Path, rng: random.Random
) -> None:
    """Синтез одной фразы Piper'ом со случайными темпом/шумностью в файл out_wav."""
    cmd = [
        piper_bin,
        "--model", str(voice),
        "--output_file", str(out_wav),
        "--length_scale", str(rng.choice(LENGTH_SCALES)),
        "--noise_scale", str(rng.choice(NOISE_SCALES)),
        "--noise_w", str(rng.choice(NOISE_WIDTHS)),
    ]
    subprocess.run(  # noqa: S603 — вход фиксирован (наша фраза), не пользовательский shell
        cmd, input=phrase.encode(), check=True, capture_output=True
    )


def _to_16k_mono(path: Path) -> None:
    """Привести WAV к 16 кГц/16-бит/моно на месте (Piper обычно выдаёт 22050/моно)."""
    with wave.open(str(path), "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        pcm = w.readframes(w.getnframes())
    if channels > 1:
        pcm = audioop.tomono(pcm, width, 0.5, 0.5)
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
        width = 2
    if rate != TARGET_RATE:
        pcm, _ = audioop.ratecv(pcm, width, 1, rate, TARGET_RATE, None)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(pcm)


def generate(
    *, phrase: str, out_dir: Path, count: int, voices: list[Path], piper_bin: str, seed: int
) -> int:
    """Сгенерировать count позитивных сэмплов, чередуя голоса. Возвращает число созданных."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    made = 0
    for i in range(count):
        voice = voices[i % len(voices)]
        out_wav = out_dir / f"friday_{i:05d}.wav"
        try:
            _synthesize(piper_bin, voice, phrase, out_wav, rng)
            _to_16k_mono(out_wav)
            made += 1
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
            print(f"✗ сэмпл {i}: Piper упал ({voice.name}): {stderr.strip()}", file=sys.stderr)
        if made and made % 100 == 0:
            print(f"  … {made}/{count}")
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phrase", default=DEFAULT_PHRASE, help="фраза активации")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data" / "wake" / "positive")
    parser.add_argument("--count", type=int, default=1000, help="сколько сэмплов")
    parser.add_argument("--models-dir", type=Path, default=ROOT / "models")
    parser.add_argument("--piper-bin", default=str(ROOT / "vendor" / "piper" / "piper"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    voices = _find_voices(args.models_dir)
    if not voices:
        print(
            f"Нет голосов Piper в {args.models_dir}. Поставь хотя бы пару русских голосов:\n"
            "  scripts/install-piper.sh dmitri && scripts/install-piper.sh irina",
            file=sys.stderr,
        )
        return 1
    if not Path(args.piper_bin).exists():
        print(f"Не найден Piper: {args.piper_bin} (см. scripts/install-piper.sh)", file=sys.stderr)
        return 1

    print(f"Голоса ({len(voices)}): {', '.join(v.stem for v in voices)}")
    print(f"Генерирую {args.count} сэмплов «{args.phrase}» → {args.out_dir}")
    made = generate(
        phrase=args.phrase,
        out_dir=args.out_dir,
        count=args.count,
        voices=voices,
        piper_bin=args.piper_bin,
        seed=args.seed,
    )
    print(f"✓ готово: {made}/{args.count} сэмплов в {args.out_dir}")
    print("Дальше — обучение модели: docs/wake-word-training.md")
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
