#!/usr/bin/env python3
"""Запись живых сэмплов wake-word «Пятница» с микрофона (самые важные данные для модели).

Синтетические позитивы (Yandex/Piper) дают обобщение, но wake-word должен надёжно срабатывать
на ТВОЙ голос/микрофон/акустику — этого синтетика не покрывает. Здесь пишем 30–50 своих «Пятница».

По Enter пишет фиксированное окно (по умолч. 1.5 с) в 16 кГц/16-бит/моно и кладёт в тот же
каталог позитивов (префикс friday_live_), чтобы попасть в обучение. Говори слово сразу после
Enter, чуть меняй интонацию/громкость/расстояние до микрофона — это повышает устойчивость.

Пример:
  scripts/record-wakeword.py --count 40
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_RATE = 16000


def _next_index(out_dir: Path, prefix: str) -> int:
    existing = sorted(out_dir.glob(f"{prefix}_*.wav"))
    if not existing:
        return 0
    last = existing[-1].stem.rsplit("_", 1)[-1]
    return int(last) + 1 if last.isdigit() else len(existing)


def _save_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(pcm)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phrase", default="Пятница")
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seconds", type=float, default=1.5)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data" / "wake" / "positive")
    parser.add_argument("--prefix", default="friday_live")
    args = parser.parse_args()

    try:
        import sounddevice as sd
    except OSError as exc:
        print(f"Аудио недоступно: {exc} (нужен libportaudio2)", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    start = _next_index(args.out_dir, args.prefix)
    frames = int(args.seconds * TARGET_RATE)
    print(
        f"Записываю {args.count} сэмплов «{args.phrase}» ({args.seconds}s) → {args.out_dir}\n"
        "Говори слово СРАЗУ после Enter. Меняй интонацию/громкость/расстояние.\n"
        "Ctrl+C — прервать (уже записанные сохранены).\n"
    )
    made = 0
    for i in range(args.count):
        idx = start + i
        try:
            input(f"[{i + 1}/{args.count}] Enter → запись…")
        except (EOFError, KeyboardInterrupt):
            print("\nпрервано")
            break
        recording = sd.rec(frames, samplerate=TARGET_RATE, channels=1, dtype="int16")
        sd.wait()
        _save_wav(args.out_dir / f"{args.prefix}_{idx:04d}.wav", recording.tobytes())
        made += 1
        print("  ✓ сохранено")

    print(f"\nГотово: записано {made} живых сэмплов в {args.out_dir}")
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
