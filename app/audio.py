from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.lrc import SubtitleLine


SAMPLE_WIDTH = 2
SAMPLE_MAX = 32767


@dataclass(frozen=True)
class AudioInfo:
    duration_ms: int
    sample_rate: int
    channels: int


def ensure_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("需要先安装 ffmpeg，并确保 ffmpeg/ffprobe 已加入 PATH。")


def probe_audio(path: Path) -> AudioInfo:
    ensure_ffmpeg()
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    payload = json.loads(result.stdout)
    duration_seconds = float(payload["format"]["duration"])
    stream = next((item for item in payload.get("streams", []) if "sample_rate" in item), {})
    return AudioInfo(
        duration_ms=int(round(duration_seconds * 1000)),
        sample_rate=int(stream.get("sample_rate", settings.sample_rate)),
        channels=int(stream.get("channels", settings.channels)),
    )


def convert_to_internal_wav(source: Path, destination: Path) -> None:
    ensure_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        str(settings.channels),
        "-ar",
        str(settings.sample_rate),
        "-sample_fmt",
        "s16",
        "-acodec",
        "pcm_s16le",
        str(destination),
    ]
    _run(command)


def encode_output(source_wav: Path, destination: Path, fmt: str) -> None:
    ensure_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "mp3":
        command = ["ffmpeg", "-y", "-i", str(source_wav), "-codec:a", "libmp3lame", "-q:a", "2", str(destination)]
    else:
        command = ["ffmpeg", "-y", "-i", str(source_wav), str(destination)]
    _run(command)


def write_binary_audio_to_wav(source_bytes: bytes, source_path: Path, wav_path: Path) -> None:
    source_path.write_bytes(source_bytes)
    convert_to_internal_wav(source_path, wav_path)


def compose_replacement_audio(
    original_wav: Path,
    segments: list[tuple[SubtitleLine, Path]],
    output_wav: Path,
    work_dir: Path,
) -> list[str]:
    original = _read_wav_samples(original_wav)
    output = array("h", original)
    warnings: list[str] = []

    for subtitle, tts_wav in segments:
        target_frames = _ms_to_frames(subtitle.end_ms - subtitle.start_ms)
        start_frame = _ms_to_frames(subtitle.start_ms)
        end_frame = min(start_frame + target_frames, len(output) // settings.channels)
        if end_frame <= start_frame:
            warnings.append(f"第 {subtitle.source_line} 行目标时间段无效，已跳过合成。")
            continue

        adjusted_wav = work_dir / f"adjusted_{subtitle.index:04d}.wav"
        prepare_tts_for_slot(
            tts_wav=tts_wav,
            reference_samples=original,
            start_frame=start_frame,
            end_frame=end_frame,
            output_wav=adjusted_wav,
        )
        tts = _read_wav_samples(adjusted_wav)
        slot_sample_start = start_frame * settings.channels
        slot_sample_end = end_frame * settings.channels
        for sample_index in range(slot_sample_start, slot_sample_end):
            output[sample_index] = 0

        max_samples = min(len(tts), slot_sample_end - slot_sample_start)
        for offset in range(max_samples):
            output[slot_sample_start + offset] = _clamp_sample(tts[offset])

    _write_wav_samples(output_wav, output)
    return warnings


def prepare_tts_for_slot(
    tts_wav: Path,
    reference_samples: array,
    start_frame: int,
    end_frame: int,
    output_wav: Path,
) -> None:
    trimmed = output_wav.with_name(output_wav.stem + "_trimmed.wav")
    normalized = output_wav.with_name(output_wav.stem + "_normalized.wav")
    stretched = output_wav.with_name(output_wav.stem + "_stretched.wav")

    tts_samples = trim_silence(_read_wav_samples(tts_wav))
    segment_samples = reference_samples[start_frame * settings.channels : end_frame * settings.channels]
    target_dbfs = rms_dbfs(segment_samples) if rms_dbfs(segment_samples) > -60 else settings.target_lufs_fallback
    gain_db = max(-18.0, min(18.0, target_dbfs - rms_dbfs(tts_samples)))
    tts_samples = apply_gain(tts_samples, gain_db)
    _write_wav_samples(trimmed, tts_samples)

    slot_ms = _frames_to_ms(end_frame - start_frame)
    current_ms = audio_duration_ms(trimmed)
    if current_ms > slot_ms and current_ms > 0:
        ratio = current_ms / max(1, slot_ms)
        tempo = min(1.20, max(0.85, ratio))
        if tempo > 1.01:
            _atempo(trimmed, stretched, tempo)
            normalized = stretched
        else:
            normalized = trimmed
    else:
        normalized = trimmed

    adjusted_samples = _read_wav_samples(normalized)
    slot_samples = (end_frame - start_frame) * settings.channels
    if len(adjusted_samples) > slot_samples:
        adjusted_samples = adjusted_samples[:slot_samples]
    elif len(adjusted_samples) < slot_samples:
        adjusted_samples.extend([0] * (slot_samples - len(adjusted_samples)))
    _write_wav_samples(output_wav, adjusted_samples)


def trim_silence(samples: array, threshold: int = 500, padding_ms: int = 40) -> array:
    if not samples:
        return samples
    frame_count = len(samples) // settings.channels
    first = 0
    last = frame_count - 1
    while first < frame_count and _frame_peak(samples, first) < threshold:
        first += 1
    while last > first and _frame_peak(samples, last) < threshold:
        last -= 1
    padding = _ms_to_frames(padding_ms)
    first = max(0, first - padding)
    last = min(frame_count - 1, last + padding)
    return samples[first * settings.channels : (last + 1) * settings.channels]


def rms_dbfs(samples: array) -> float:
    if not samples:
        return -90.0
    square_sum = sum(sample * sample for sample in samples)
    rms = math.sqrt(square_sum / len(samples))
    if rms <= 0:
        return -90.0
    return 20 * math.log10(rms / SAMPLE_MAX)


def apply_gain(samples: array, gain_db: float) -> array:
    factor = 10 ** (gain_db / 20)
    gained = array("h")
    gained.extend(_clamp_sample(int(round(sample * factor))) for sample in samples)
    return gained


def audio_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return _frames_to_ms(handle.getnframes())


def _read_wav_samples(path: Path) -> array:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != settings.channels:
            raise ValueError(f"{path} 声道数不是 {settings.channels}")
        if handle.getframerate() != settings.sample_rate:
            raise ValueError(f"{path} 采样率不是 {settings.sample_rate}")
        if handle.getsampwidth() != SAMPLE_WIDTH:
            raise ValueError(f"{path} 不是 16-bit PCM")
        samples = array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))
    return samples


def _write_wav_samples(path: Path, samples: array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(settings.channels)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(settings.sample_rate)
        handle.writeframes(samples.tobytes())


def _atempo(source: Path, destination: Path, tempo: float) -> None:
    command = ["ffmpeg", "-y", "-i", str(source), "-filter:a", f"atempo={tempo:.6f}", str(destination)]
    _run(command)


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"命令执行失败：{' '.join(command)}")


def _frame_peak(samples: array, frame: int) -> int:
    start = frame * settings.channels
    return max(abs(samples[start + channel]) for channel in range(settings.channels))


def _ms_to_frames(ms: int) -> int:
    return int(round(ms * settings.sample_rate / 1000))


def _frames_to_ms(frames: int) -> int:
    return int(round(frames * 1000 / settings.sample_rate))


def _clamp_sample(value: int) -> int:
    return max(-32768, min(32767, value))
