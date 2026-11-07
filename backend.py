"""FFmpeg conversion backend shared by the desktop interface."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time

from converter import find_ffmpeg


@dataclass(frozen=True)
class FormatProfile:
    label: str
    extension: str
    video_codecs: tuple[str, ...]
    audio_codec: str
    dvd_size: tuple[int, int] | None = None


@dataclass(frozen=True)
class EncodingOptions:
    profile: str
    quality: str
    codec: str | None
    resolution: tuple[int, int] | None


PROFILES = {
    "mp4": FormatProfile("MP4 · универсальное видео", ".mp4", ("h264", "hevc"), "aac"),
    "mkv": FormatProfile("MKV · видеоархив", ".mkv", ("h264", "hevc", "vp9"), "aac"),
    "mov": FormatProfile("MOV · QuickTime", ".mov", ("h264", "hevc"), "aac"),
    "avi": FormatProfile("AVI · MPEG-4", ".avi", ("mpeg4",), "libmp3lame"),
    "webm": FormatProfile("WebM · для браузера", ".webm", ("vp9",), "libopus"),
    "mp3": FormatProfile("MP3 · только звук", ".mp3", (), "libmp3lame"),
    "pal-dvd": FormatProfile("DVD PAL · MPEG-2", ".mpg", ("mpeg2",), "ac3", (720, 576)),
    "ntsc-dvd": FormatProfile("DVD NTSC · MPEG-2", ".mpg", ("mpeg2",), "ac3", (720, 480)),
}
DVD_TARGETS = {key: profile.dvd_size for key, profile in PROFILES.items() if profile.dvd_size}


INPUT_EXTENSIONS = {
    ".avi", ".mp4", ".mkv", ".mov", ".webm", ".wmv", ".m4v",
    ".mpeg", ".mpg", ".flv", ".ts", ".m2ts", ".mts", ".3gp",
    ".3g2", ".ogv", ".vob", ".mxf", ".mp3", ".wav", ".flac",
    ".aac", ".m4a", ".ogg", ".opus", ".wma", ".aiff", ".aif",
}

_QUALITY = {
    "high": {"h264_crf": 18, "vp9_crf": 24, "mpeg4_q": 2, "mp3_q": 0, "audio_bitrate": "256k"},
    "balanced": {"h264_crf": 23, "vp9_crf": 32, "mpeg4_q": 5, "mp3_q": 2, "audio_bitrate": "192k"},
    "small": {"h264_crf": 28, "vp9_crf": 40, "mpeg4_q": 8, "mp3_q": 5, "audio_bitrate": "128k"},
}
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


class ProbeTimeout(RuntimeError):
    pass


class ConversionCancelled(RuntimeError):
    """The caller cancelled a conversion before its output was published."""


def _get_profile(profile: str) -> FormatProfile:
    if profile not in PROFILES:
        raise ValueError(f"Неизвестный формат: {profile}")
    return PROFILES[profile]


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def output_path(
    source: str | Path,
    folder: str | Path,
    profile: str,
    reserved: Iterable[str | Path] | None = None,
    *,
    name: str | None = None,
) -> Path:
    """Choose a free name; also avoid the input and outputs queued by the GUI."""
    config = _get_profile(profile)
    source = Path(source).expanduser().resolve()
    folder = Path(folder).expanduser().resolve()
    used = {_path_key(source)}
    if reserved is not None:
        used.update(_path_key(path) for path in reserved)
    stem = source.stem if name is None else output_stem(name, profile)
    candidate = folder / f"{stem}{config.extension}"
    number = 2
    while os.path.lexists(candidate) or _path_key(candidate) in used:
        candidate = folder / f"{stem} ({number}){config.extension}"
        number += 1
    return candidate


def output_stem(name: str, profile: str) -> str:
    extension = _get_profile(profile).extension
    stem = name.strip()
    if stem.lower().endswith(extension):
        stem = stem[:-len(extension)].rstrip()
    if (not stem or stem.endswith(".") or re.search(r'[<>:"/\\|?*\x00-\x1f]', stem)
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])", stem.split(".")[0].rstrip())):
        raise ValueError(f"Недопустимое имя файла: {name!r}")
    return stem


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ConversionCancelled("Конвертация отменена.")


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    process.wait()


def _communicate(process: subprocess.Popen, cancel: threading.Event | None, timeout: float):
    deadline = time.monotonic() + timeout
    while True:
        _check_cancel(cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            return process.communicate(timeout=min(0.1, remaining))
        except subprocess.TimeoutExpired:
            continue


def _probe(ffmpeg: str, source: Path, cancel: threading.Event | None) -> str:
    """Use the bundled FFmpeg itself; ffprobe is not a separate dependency."""
    _check_cancel(cancel)
    process = subprocess.Popen(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(source)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=_HIDDEN,
    )
    try:
        _, stderr = _communicate(process, cancel, timeout=15)
        return stderr.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise ProbeTimeout("FFmpeg не успел прочитать сведения о файле.") from exc
    finally:
        _stop_process(process)
        if process.stderr is not None:
            process.stderr.close()


def media_info(source: Path) -> str:
    lines = _probe(find_ffmpeg(), source.resolve(), None).splitlines()
    details = [line.strip() for line in lines if line.lstrip().startswith(("Duration:", "Stream #"))]
    if not details:
        raise ValueError("Не удалось прочитать дорожки файла.")
    return "\n".join(line.replace("Duration:", "Длительность:") for line in details)


VIDEO_CODECS = {
    "h264": ("H.264", "libx264"),
    "hevc": ("H.265 / HEVC", "libx265"),
    "vp9": ("VP9", "libvpx-vp9"),
    "mpeg4": ("MPEG-4", "mpeg4"),
    "mpeg2": ("MPEG-2", "mpeg2video"),
}


def supported_video_codecs(profile: str) -> tuple[str, ...]:
    return _get_profile(profile).video_codecs


GPU_ENCODERS = {
    "h264": ("h264_nvenc", "h264_qsv", "h264_amf"),
    "hevc": ("hevc_nvenc", "hevc_qsv", "hevc_amf"),
    "vp9": ("vp9_qsv",),
}
ENCODER_NAMES = {"nvenc": "NVIDIA NVENC", "qsv": "Intel Quick Sync", "amf": "AMD AMF"}


def _gpu_options(encoder: str, quality: str) -> list[str]:
    level = {"high": 18, "balanced": 23, "small": 28}[quality]
    if encoder.endswith("_nvenc"):
        return ["-preset", "p4", "-rc", "vbr", "-cq", str(level), "-b:v", "0"]
    if encoder.endswith("_qsv"):
        return ["-preset", "fast", "-global_quality", str(level)]
    return ["-quality", "speed", "-rc", "cqp", "-qp_i", str(level), "-qp_p", str(level)]


_GPU_CHECKS: dict[tuple[str, str, str], bool] = {}


def _usable_gpu(ffmpeg: str, encoder: str, quality: str, cancel: threading.Event | None) -> bool:
    _check_cancel(cancel)
    key = (ffmpeg, encoder, quality)
    if key in _GPU_CHECKS:
        return _GPU_CHECKS[key]
    process = subprocess.Popen(
        [ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
         "color=s=256x256:r=25,format=nv12", "-frames:v", "3", "-c:v", encoder,
         *_gpu_options(encoder, quality), "-f", "null", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_HIDDEN,
    )
    try:
        _communicate(process, cancel, timeout=5)
        _check_cancel(cancel)
        _GPU_CHECKS[key] = process.returncode == 0
        return _GPU_CHECKS[key]
    except subprocess.TimeoutExpired:
        return False
    finally:
        _stop_process(process)


def _fit_filter(width: int, height: int, sar: str = "1/1") -> str:
    # Fit the display aspect ratio inside the output canvas, including anamorphic sources.
    return (
        f"scale=w='max(2,2*trunc(min({width},{height}*dar/({sar}))/2))'"
        f":h='max(2,2*trunc(min({height},{width}*({sar})/dar)/2))',"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar={sar}"
    )


def _codec_arguments(options: EncodingOptions, gpu: str | None, pipeline: str) -> list[str]:
    profile, quality = options.profile, options.quality
    codec, resolution = options.codec, options.resolution
    config, settings = PROFILES[profile], _QUALITY[quality]
    if not config.video_codecs:
        return ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-ac", "2", "-q:a", str(settings["mp3_q"])]

    if profile in DVD_TARGETS:
        width, height = DVD_TARGETS[profile]
        sar = "16/15" if profile == "pal-dvd" else "8/9"
        return [
            "-map", "0:v:0", "-map", "0:a:0?", "-target", profile,
            "-vf", _fit_filter(width, height, sar), "-aspect", "4:3",
            "-ac", "2", "-b:a", "192k",
        ]

    video_filter = _fit_filter(*resolution) if resolution else "pad=ceil(iw/2)*2:ceil(ih/2)*2"
    if pipeline == "d3d11":
        video_filter = "hwdownload,format=nv12|p010le," + video_filter
    cuda = pipeline in {"cuda", "cuda_upload"}
    if cuda:
        dimensions = f"w={resolution[0]}:h={resolution[1]}:" if resolution else ""
        video_filter = f"scale_cuda={dimensions}format=nv12"
        if pipeline == "cuda_upload":
            video_filter = "format=nv12,hwupload_cuda," + video_filter
    args = [
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", gpu or VIDEO_CODECS[codec][1],
        "-pix_fmt", "cuda" if cuda else ("nv12" if gpu else "yuv420p"), "-vf", video_filter,
    ]
    if gpu:
        args += _gpu_options(gpu, quality)
    elif codec in {"h264", "hevc"}:
        args += ["-preset", "fast", "-crf", str(settings["h264_crf"])]
    elif codec == "vp9":
        args += ["-crf", str(settings["vp9_crf"]), "-b:v", "0", "-deadline", "good", "-cpu-used", "5", "-row-mt", "1"]
    else:
        args += ["-q:v", str(settings["mpeg4_q"])]
    args += ["-c:a", config.audio_codec, "-b:a", settings["audio_bitrate"]]
    if profile == "avi":
        args += ["-ac", "2", "-vtag", "XVID"]
    if profile in {"mp4", "mov"}:
        args += ["-movflags", "+faststart"]
        if codec == "hevc":
            args += ["-tag:v", "hvc1"]
    return args


def _encoding_options(profile, quality, video_codec, resolution) -> EncodingOptions:
    if quality not in _QUALITY:
        raise ValueError(f"Неизвестное качество: {quality}")
    codecs = supported_video_codecs(profile)
    codec = video_codec or (codecs[0] if codecs else None)
    if codec is not None and codec not in codecs:
        raise ValueError("Видеокодек несовместим с выбранным форматом.")
    if resolution is not None:
        if len(resolution) != 2 or any(not isinstance(n, int) or n < 2 or n % 2 for n in resolution):
            raise ValueError("Ширина и высота должны быть положительными чётными числами.")
        if not codec or (profile in DVD_TARGETS and resolution != DVD_TARGETS[profile]):
            raise ValueError("Это разрешение несовместимо с выбранным форматом.")
    return EncodingOptions(profile, quality, codec, resolution)


def _gpu_candidates(ffmpeg, options: EncodingOptions, acceleration, cancel) -> Iterable[str]:
    if acceleration == "cpu":
        return
    for encoder in GPU_ENCODERS.get(options.codec, ()):
        usable = _usable_gpu(ffmpeg, encoder, options.quality, cancel)
        _check_cancel(cancel)
        if usable:
            yield encoder


def _duration_seconds(probe: str) -> float | None:
    match = _DURATION.search(probe)
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _cuda_source(probe: str, resolution: tuple[int, int] | None) -> str | None:
    # The bundled scale_cuda can resize but cannot add borders. Use the
    # existing software filter when rotation, padding or a SAR change is needed.
    if re.search(r"rotation of (?!0(?:\.0+)? degrees)", probe, re.IGNORECASE):
        return None
    stream = next((line for line in probe.splitlines() if "Stream #" in line and " Video: " in line), "")
    match = re.search(r"Video: (\w+).*?,\s*(\d+)x(\d+)\b", stream)
    if not match:
        return None
    codec, width, height = match.groups()
    width, height = int(width), int(height)
    if width % 2 or height % 2:
        return None
    if resolution:
        sar = re.search(r"\bSAR (\d+):(\d+)", stream)
        if (sar and sar[1] != sar[2]) or width * resolution[1] != height * resolution[0]:
            return None
    return codec


def _encoding_attempts(gpus: Iterable[str], acceleration: str, source_codec: str | None):
    """Return preferred pipelines, followed by permitted fallbacks."""
    for gpu in gpus:
        if gpu.endswith("_nvenc") and source_codec:
            if acceleration == "gpu" or (
                acceleration == "auto" and source_codec in {"h264", "hevc", "av1", "vp8", "vp9", "mpeg2video", "mjpeg"}
            ):
                yield gpu, "cuda"
            if acceleration != "gpu":
                yield gpu, "cuda_upload"
        yield gpu, "d3d11" if acceleration == "gpu" else "software"
    if acceleration in {"cpu", "auto"}:
        yield None, "software"


def _encoder_label(gpu, pipeline, retry):
    if not gpu:
        return "Процессор · резервный режим" if retry else "Процессор"
    vendor = ENCODER_NAMES[gpu.rsplit("_", 1)[-1]]
    mode = {
        "cuda": "NVDEC → CUDA → NVENC",
        "cuda_upload": "CPU → CUDA → NVENC",
        "d3d11": f"D3D11VA → {vendor}",
        "software": f"CPU → {vendor}",
    }
    return mode[pipeline]


def _read_progress(stream, messages: queue.Queue) -> None:
    try:
        for line in stream:
            messages.put(line)
    finally:
        messages.put(None)


def _error_tail(log) -> str:
    log.flush()
    log.seek(0, os.SEEK_END)
    end = log.tell()
    log.seek(max(0, end - 8000))
    return log.read().decode("utf-8", errors="replace").strip()


def convert_file(
    source: str | Path,
    target: str | Path,
    profile: str,
    quality: str = "balanced",
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    *,
    video_codec: str | None = None,
    acceleration: str = "cpu",
    encoder_status: Callable[[str], None] | None = None,
    resolution: tuple[int, int] | None = None,
) -> Path:
    """Convert one file, reporting percentages; publish only complete output.

    The callback runs on the calling thread. Desktop applications should call
    this function in a worker and forward progress to their UI thread.
    """
    config = _get_profile(profile)
    options = _encoding_options(profile, quality, video_codec, resolution)
    if acceleration not in {"auto", "cpu", "gpu", "hybrid"}:
        raise ValueError("Неизвестный режим ускорения.")
    source = Path(source).expanduser().resolve()
    requested_target = Path(target).expanduser().absolute()
    target = requested_target.parent.resolve() / requested_target.name
    if not source.is_file():
        raise ValueError(f"Исходный файл не найден: {source}")
    if source.suffix.lower() not in INPUT_EXTENSIONS:
        raise ValueError(f"Неподдерживаемый тип исходного файла: {source.suffix}")
    if target.suffix.lower() != config.extension:
        raise ValueError(f"Для выбранного формата требуется расширение {config.extension}.")
    if _path_key(source) == _path_key(target) or os.path.lexists(target):
        raise ValueError(f"Файл уже существует, выберите другое имя: {target}")
    _check_cancel(cancel)
    ffmpeg = find_ffmpeg()
    if progress is not None:
        progress(0.0)
    try:
        probe = _probe(ffmpeg, source, cancel)
    except ProbeTimeout:
        probe = ""
    duration = _duration_seconds(probe)
    _check_cancel(cancel)
    gpus = _gpu_candidates(ffmpeg, options, acceleration, cancel)
    attempts = _encoding_attempts(gpus, acceleration, _cuda_source(probe, resolution))
    error = None
    for encoder, pipeline in attempts:
        _check_cancel(cancel)
        if error is not None and progress:
            progress(0.0)
        if encoder_status:
            encoder_status(_encoder_label(encoder, pipeline, retry=error is not None))
        try:
            _encode_to_target(ffmpeg, source, target, options, encoder, duration, progress, cancel, pipeline)
            break
        except EncodingError as exc:
            error = exc
    else:
        _check_cancel(cancel)
        if error is None:
            raise ValueError(
                "Для выбранного кодека аппаратное кодирование недоступно. Выберите «Авто» или «Процессор»."
            )
        if acceleration == "gpu":
            raise EncodingError(
                f"Режим «Видеокарта» не смог обработать файл. Попробуйте «Комбинированный».\n{error}"
            ) from error
        raise error
    if progress is not None:
        progress(100.0)
    return target


def _encode_to_target(ffmpeg, source, target, options, gpu, duration, progress, cancel, pipeline):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".video-convert-", dir=target.parent) as directory:
        temporary = Path(directory) / f"result{target.suffix}"
        _encode(ffmpeg, source, temporary, options, gpu, duration, progress, cancel, pipeline=pipeline)
        if os.path.lexists(target):
            raise ValueError(f"Выходной файл уже появился: {target}")
        _check_cancel(cancel)
        # Windows rename is atomic on this volume and never overwrites a file.
        temporary.rename(target)


class EncodingError(RuntimeError):
    pass


def _encode(ffmpeg, source, temporary, options, gpu, duration, progress, cancel, *, pipeline):
    decoder = {
        "cuda": ["-hwaccel:v:0", "cuda", "-hwaccel_output_format:v:0", "cuda", "-extra_hw_frames:v:0", "8"],
        "d3d11": ["-hwaccel:v:0", "d3d11va", "-hwaccel_output_format:v:0", "d3d11"],
    }.get(pipeline, [])
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-n", "-nostats",
        "-progress", "pipe:1", "-stats_period", "0.2", *decoder, "-i", str(source),
        *_codec_arguments(options, gpu, pipeline), str(temporary),
    ]
    with temporary.with_name("ffmpeg.log").open("w+b") as log:
        _check_cancel(cancel)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_HIDDEN,
        )
        messages: queue.Queue = queue.Queue()
        reader = threading.Thread(target=_read_progress, args=(process.stdout, messages), daemon=True)
        last_percent = 0.0
        try:
            reader.start()
            while True:
                _check_cancel(cancel)
                try:
                    line = messages.get(timeout=0.1)
                except queue.Empty:
                    if process.poll() is not None and not reader.is_alive():
                        break
                    continue
                if line is None:
                    break
                key, _, value = line.strip().partition("=")
                if key in {"out_time_us", "out_time_ms"} and duration and progress is not None:
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        continue
                    percent = max(last_percent, min(99.9, seconds / duration * 100))
                    if percent > last_percent:
                        progress(percent)
                        last_percent = percent
            while process.poll() is None:
                _check_cancel(cancel)
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            _check_cancel(cancel)
            if process.returncode:
                detail = _error_tail(log)
                raise EncodingError(f"FFmpeg не смог преобразовать файл.\n{detail}")
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise EncodingError("FFmpeg не создал выходной файл.")
        finally:
            _stop_process(process)
            if reader.ident is not None:
                reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
