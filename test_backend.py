"""Real FFmpeg integration checks; artifacts stay under output/qa/gui."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

import imageio_ffmpeg

import backend


ROOT = Path(__file__).resolve().parent
QA_ROOT = ROOT / "output" / "qa" / "gui"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
os.environ["FFMPEG_EXE"] = FFMPEG
EXPECTED_CODECS = {
    "mp4": ("h264", "aac"), "mkv": ("h264", "aac"),
    "mov": ("h264", "aac"), "avi": ("mpeg4", "mp3"),
    "webm": ("vp9", "opus"), "mp3": (None, "mp3"),
    "pal-dvd": ("mpeg2video", "ac3"), "ntsc-dvd": ("mpeg2video", "ac3"),
}


def artifact_folder(name: str) -> Path:
    path = QA_ROOT / f"{name}-{time.time_ns()}"
    path.mkdir(parents=True)
    return path


def ffmpeg_run(arguments: list[str], log: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostdin", *arguments],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    return result


def make_source(folder: Path, *, long: bool = False) -> Path:
    target = folder / ("long.avi" if long else "sample.avi")
    if target.exists():
        return target
    size, rate, duration = ("960x540", "30", "12") if long else ("160x120", "10", "1")
    result = ffmpeg_run([
        "-n", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
        "-t", duration, "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
        "-c:v", "mpeg4", "-q:v", "5", "-c:a", "pcm_s16le", str(target),
    ], folder / (target.stem + "-creation.log"))
    if result.returncode:
        raise RuntimeError(result.stderr)
    return target


def inspect_and_decode(target: Path, expected_profile: str) -> dict:
    probe = ffmpeg_run(["-i", str(target)], target.with_name(target.name + ".probe.log"))
    videos = re.findall(r"Stream #.*?: Video: ([^,\s]+)", probe.stderr)
    audios = re.findall(r"Stream #.*?: Audio: ([^,\s]+)", probe.stderr)
    video_codec, audio_codec = EXPECTED_CODECS[expected_profile]
    expected_videos = [] if video_codec is None else [video_codec]
    expected_audios = [audio_codec] * (1 if expected_profile in {"mp3", "pal-dvd", "ntsc-dvd"} else 2)
    if videos != expected_videos or audios != expected_audios:
        raise AssertionError(f"{target.name}: streams {videos}/{audios}, expected {expected_videos}/{expected_audios}")
    decoded = ffmpeg_run([
        "-v", "error", "-i", str(target), "-map", "0:v?", "-map", "0:a?",
        "-f", "null", "-",
    ], target.with_name(target.name + ".decode.log"))
    if decoded.returncode or decoded.stderr.strip():
        raise AssertionError(f"{target.name}: decode failed: {decoded.stderr}")
    return {"file": str(target), "video_codecs": videos, "audio_codecs": audios,
            "decode_returncode": decoded.returncode, "bytes": target.stat().st_size}


class BackendIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = artifact_folder("backend")
        cls.source = make_source(cls.folder)
        cls.source_hash = hashlib.sha256(cls.source.read_bytes()).hexdigest()
        cls.facts = []

    @classmethod
    def tearDownClass(cls):
        cls.facts.append({"source_unchanged": hashlib.sha256(cls.source.read_bytes()).hexdigest() == cls.source_hash})
        (cls.folder / "results.json").write_text(json.dumps(cls.facts, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_all_profiles_and_audio_tracks(self):
        self.assertEqual(set(backend.PROFILES), set(EXPECTED_CODECS))
        for profile in EXPECTED_CODECS:
            with self.subTest(profile=profile):
                target = backend.output_path(self.source, self.folder / "profiles", profile)
                progress = []
                backend.convert_file(self.source, target, profile, progress=progress.append)
                facts = inspect_and_decode(target, profile)
                if profile in backend.DVD_TARGETS:
                    width, height = backend.DVD_TARGETS[profile]
                    details = backend.media_info(target)
                    self.assertIn(f"{width}x{height}", details)
                    self.assertIn("DAR 4:3", details)
                    self.assertIn("25 fps" if profile == "pal-dvd" else "29.97 fps", details)
                self.assertTrue(progress)
                self.assertEqual(progress[0], 0)
                self.assertEqual(progress[-1], 100)
                self.assertEqual(progress, sorted(progress))
                self.assertTrue(all(0 <= value <= 100 for value in progress))
                self.facts.append({"profile": profile, "progress": progress, **facts})

    def test_gpu_fallback_tries_another_encoder_before_cpu(self):
        target = self.folder / "next-encoder.mp4"
        attempts = []
        encode_on_cpu = backend._encode

        def encode(ffmpeg, source, temporary, options, gpu, *args, pipeline):
            attempts.append(gpu)
            if gpu != "h264_qsv":
                temporary.write_bytes(b"partial")
                raise backend.EncodingError("simulated NVENC failure")
            encode_on_cpu(ffmpeg, source, temporary, options, None, *args, pipeline="software")

        with patch.object(backend, "_usable_gpu", return_value=True), \
             patch.object(backend, "_encode", side_effect=encode):
            backend.convert_file(self.source, target, "mp4", acceleration="hybrid")
        self.assertEqual(attempts, ["h264_nvenc", "h264_nvenc", "h264_qsv"])
        inspect_and_decode(target, "mp4")
        self.assertFalse(list(self.folder.glob(".video-convert-*")))

    def test_cancel_during_gpu_detection_is_not_an_encoding_error(self):
        cancel = threading.Event()
        target = self.folder / "cancel-probe.mp4"

        def probe(*args):
            cancel.set()
            return False

        with patch.object(backend, "_usable_gpu", side_effect=probe):
            with self.assertRaises(backend.ConversionCancelled):
                backend.convert_file(self.source, target, "mp4", acceleration="gpu", cancel=cancel)
        self.assertFalse(target.exists())

    def test_existing_output_missing_source_and_duplicate_names(self):
        folder = self.folder / "duplicates"
        folder.mkdir()
        first = backend.output_path(self.source, folder, "mp4")
        first.write_bytes(b"existing output must survive")
        second = backend.output_path(self.source, folder, "mp4")
        third = backend.output_path(self.source, folder, "mp4", {second})
        self.assertEqual(second.name, "sample (2).mp4")
        self.assertEqual(third.name, "sample (3).mp4")
        with self.assertRaises(ValueError):
            backend.convert_file(self.source, first, "mp4")
        self.assertEqual(first.read_bytes(), b"existing output must survive")
        missing_output = folder / "missing.mp4"
        with self.assertRaises(ValueError):
            backend.convert_file(folder / "missing.avi", missing_output, "mp4")
        self.assertFalse(missing_output.exists())
        same_extension = backend.output_path(self.source, self.source.parent, "avi")
        self.assertNotEqual(same_extension, self.source)
        self.facts.append({"duplicate_names": [first.name, second.name, third.name], "existing_output_preserved": True, "missing_source_rejected": True})

    def test_inflight_cancellation_removes_partial_output(self):
        source = make_source(self.folder, long=True)
        folder = self.folder / "cancellation"
        target = folder / "cancelled.webm"
        event = threading.Event()
        progress = []

        def cancel_after_progress(value):
            progress.append(value)
            if 0 < value < 100:
                event.set()

        started = time.monotonic()
        with self.assertRaises(backend.ConversionCancelled):
            backend.convert_file(source, target, "webm", "high", cancel_after_progress, event)
        self.assertTrue(any(0 < value < 100 for value in progress), progress)
        self.assertFalse(target.exists())
        self.assertEqual(list(folder.iterdir()), [])
        self.facts.append({"inflight_cancel_progress": progress, "cancel_elapsed_seconds": time.monotonic() - started, "partial_output_removed": True, "temporary_directory_removed": True})

    def test_corrupt_input_failure_removes_partial_output(self):
        source = self.folder / "corrupt.avi"
        source.write_bytes(b"not a media file\n")
        folder = self.folder / "failure"
        target = folder / "corrupt.mp4"
        with self.assertRaises(RuntimeError):
            backend.convert_file(source, target, "mp4")
        self.assertFalse(target.exists())
        self.assertEqual(list(folder.iterdir()), [])
        self.facts.append({"corrupt_input_rejected": True, "failed_output_removed": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
