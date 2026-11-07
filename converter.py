"""AVI to MP4 converter. Run inside the project's virtual environment."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def find_ffmpeg() -> str:
    configured = os.environ.get("FFMPEG_EXE")
    if configured:
        if not Path(configured).is_file():
            raise ValueError("FFMPEG_EXE указывает на отсутствующий файл.")
        return configured
    installed = shutil.which("ffmpeg")
    if installed:
        return installed
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise ValueError("Установите зависимости: python -m pip install -r requirements.txt") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def convert(source: Path, target: Path, *, crf: int = 20) -> Path:
    source, target = source.resolve(), target.resolve()
    if not source.is_file():
        raise ValueError(f"Исходный файл не найден: {source}")
    if source.suffix.lower() != ".avi":
        raise ValueError("Выберите видео с расширением .avi.")
    if target.suffix.lower() != ".mp4":
        raise ValueError("Выходной файл должен иметь расширение .mp4.")
    if target.exists():
        raise ValueError(f"Файл уже существует, выберите другое имя: {target}")
    if not 0 <= crf <= 51:
        raise ValueError("CRF должен быть от 0 до 51.")
    ffmpeg = find_ffmpeg()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Only publish a complete file. Temporary output stays on the same volume.
    with tempfile.TemporaryDirectory(prefix="avi-convert-", dir=target.parent) as directory:
        temporary = Path(directory) / "output.mp4"
        command = [
            ffmpeg, "-hide_banner", "-nostdin", "-n", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264",
            "-preset", "fast", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:a", "aac",
            "-b:a", "192k", "-movflags", "+faststart", str(temporary),
        ]
        print(f"Конвертация: {source}\nРезультат: {target}", flush=True)
        subprocess.run(command, check=True)
        # Windows rename refuses to replace a destination created concurrently.
        if target.exists():
            raise ValueError(f"Выходной файл уже появился: {target}")
        temporary.rename(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Конвертация AVI в MP4 (H.264 + AAC).")
    parser.add_argument("input", type=Path, help="Путь к AVI")
    parser.add_argument("-o", "--output", type=Path, help="Путь к MP4")
    parser.add_argument("--crf", type=int, default=20, help="Качество: 0–51, меньше — лучше (20 по умолчанию)")
    args = parser.parse_args()
    target = args.output or Path.home() / "Videos" / args.input.with_suffix(".mp4").name
    try:
        result = convert(args.input, target, crf=args.crf)
    except KeyboardInterrupt:
        print("\nКонвертация отменена.", file=sys.stderr)
        return 130
    except (ValueError, OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(f"Готово: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
