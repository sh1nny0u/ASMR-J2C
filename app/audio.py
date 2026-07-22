from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import settings
from app.lrc import SubtitleLine


SAMPLE_WIDTH = 2
SAMPLE_MAX = 32767
MAX_TEMPO = 1.35
SILENCE_THRESHOLD = 300
LONG_SILENCE_MS = 500
RETAINED_INTERNAL_SILENCE_MS = 180


@dataclass(frozen=True)
class AudioInfo:
    duration_ms: int
    sample_rate: int
    channels: int


@dataclass(frozen=True)
class PreparedSegment:
    subtitle: SubtitleLine
    wav_path: Path
    source_duration_ms: int
    compacted_silence_ms: int
    tempo: float
    duration_ms: int
    overflowed: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["wav_path"] = str(self.wav_path)
        payload["subtitle"] = self.subtitle.to_dict()
        return payload


@dataclass(frozen=True)
class ScheduledSegment:
    prepared: PreparedSegment
    start_ms: int
    end_ms: int
    delay_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "source_line": self.prepared.subtitle.source_line,
            "subtitle_start_ms": self.prepared.subtitle.start_ms,
            "subtitle_end_ms": self.prepared.subtitle.end_ms,
            "scheduled_start_ms": self.start_ms,
            "scheduled_end_ms": self.end_ms,
            "delay_ms": self.delay_ms,
            "source_duration_ms": self.prepared.source_duration_ms,
            "compacted_silence_ms": self.prepared.compacted_silence_ms,
            "tempo": self.prepared.tempo,
            "final_duration_ms": self.prepared.duration_ms,
            "overflowed": self.prepared.overflowed,
        }


def ensure_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("需要先安装 ffmpeg，并确保 ffmpeg 和 ffprobe 已加入 PATH。")


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
    _run(
        [
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
    )


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


def prepare_tts_for_slot(
    tts_wav: Path,
    reference_samples: array,
    subtitle: SubtitleLine,
    output_wav: Path,
) -> PreparedSegment:
    """Normalize one TTS response without ever cropping voiced tail samples."""
    raw_samples = _read_wav_samples(tts_wav)
    source_duration_ms = _frames_to_ms(len(raw_samples) // settings.channels)
    samples = trim_leading_silence(raw_samples)
    samples, compacted_frames = compact_internal_silence(samples)

    start_frame = _ms_to_frames(subtitle.start_ms)
    end_frame = _ms_to_frames(subtitle.end_ms)
    reference = reference_samples[
        min(len(reference_samples), start_frame * settings.channels) : min(len(reference_samples), end_frame * settings.channels)
    ]
    target_dbfs = rms_dbfs(reference) if rms_dbfs(reference) > -60 else settings.target_lufs_fallback
    samples = apply_gain(samples, max(-18.0, min(18.0, target_dbfs - rms_dbfs(samples))))

    prepared_wav = output_wav.with_name(output_wav.stem + "_prepared.wav")
    transformed_wav = output_wav.with_name(output_wav.stem + "_tempo.wav")
    _write_wav_samples(prepared_wav, samples)

    target_frames = max(1, end_frame - start_frame)
    source_frames = len(samples) // settings.channels
    requested_tempo = source_frames / target_frames if source_frames else 1.0
    tempo = min(MAX_TEMPO, requested_tempo) if requested_tempo > 1.0 else 1.0
    if tempo > 1.001:
        _atempo(prepared_wav, transformed_wav, tempo)
        adjusted = _read_wav_samples(transformed_wav)
    else:
        adjusted = samples

    # Short output is padded, while long output is deliberately retained and scheduled later.
    if len(adjusted) < target_frames * settings.channels:
        adjusted.extend([0] * (target_frames * settings.channels - len(adjusted)))
    _write_wav_samples(output_wav, adjusted)
    duration_ms = _frames_to_ms(len(adjusted) // settings.channels)
    return PreparedSegment(
        subtitle=subtitle,
        wav_path=output_wav,
        source_duration_ms=source_duration_ms,
        compacted_silence_ms=_frames_to_ms(compacted_frames),
        tempo=round(tempo, 6),
        duration_ms=duration_ms,
        overflowed=len(adjusted) > target_frames * settings.channels,
    )


def schedule_segments(prepared_segments: list[PreparedSegment]) -> tuple[list[ScheduledSegment], list[str]]:
    scheduled: list[ScheduledSegment] = []
    warnings: list[str] = []
    cursor_ms = 0
    for prepared in prepared_segments:
        subtitle = prepared.subtitle
        start_ms = max(subtitle.start_ms, cursor_ms)
        end_ms = start_ms + prepared.duration_ms
        delay_ms = start_ms - subtitle.start_ms
        if delay_ms:
            warnings.append(f"第 {subtitle.source_line} 行因前句超时顺延 {delay_ms} ms，以保留完整中文语音。")
        if prepared.overflowed:
            warnings.append(
                f"第 {subtitle.source_line} 行超过自然加速上限 {MAX_TEMPO:.2f}x，已保留完整语音并顺延后续中文。"
            )
        scheduled.append(ScheduledSegment(prepared, start_ms, end_ms, delay_ms))
        cursor_ms = end_ms
    return scheduled, warnings


def write_timing_metadata(path: Path, scheduled_segments: list[ScheduledSegment]) -> None:
    payload = {
        "max_tempo": MAX_TEMPO,
        "segments": [segment.to_dict() for segment in scheduled_segments],
        "diagnostic_note": "原始 TTS 响应和转换后的 WAV 已保留。若原始响应本身漏字，需要通过试听原始文件确认。",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compose_audio(
    original_wav: Path,
    segments: list[ScheduledSegment],
    output_wav: Path,
    bilingual_mode: bool = False,
    japanese_volume: float = 100.0,
    chinese_volume: float = 100.0,
) -> list[str]:
    original = _read_wav_samples(original_wav)
    required_frames = max(
        len(original) // settings.channels,
        max((_ms_to_frames(segment.end_ms) for segment in segments), default=0),
    )
    base = array("h", original)
    base.extend([0] * (required_frames * settings.channels - len(base)))
    warnings: list[str] = []

    if bilingual_mode:
        mixed = array("i", (int(round(sample * (japanese_volume / 100.0))) for sample in base))
        chinese_gain = chinese_volume / 100.0
        for segment in segments:
            _add_segment(mixed, _read_wav_samples(segment.prepared.wav_path), _ms_to_frames(segment.start_ms), chinese_gain)
        peak = max((abs(sample) for sample in mixed), default=0)
        limiter_gain = 1.0
        if peak > SAMPLE_MAX:
            limiter_gain = SAMPLE_MAX / peak
            warnings.append("双语混音峰值过高，已启用主限幅保护；请按需降低日语或中文音量滑杆。")
        output = array("h", (_clamp_sample(int(round(sample * limiter_gain))) for sample in mixed))
    else:
        output = array("h", base)
        for segment in segments:
            _replace_segment(output, _read_wav_samples(segment.prepared.wav_path), _ms_to_frames(segment.start_ms))

    _write_wav_samples(output_wav, output)
    return warnings


def trim_leading_silence(samples: array, threshold: int = SILENCE_THRESHOLD, padding_ms: int = 40) -> array:
    if not samples:
        return samples
    frame_count = len(samples) // settings.channels
    first = 0
    while first < frame_count and _frame_peak(samples, first) < threshold:
        first += 1
    if first == frame_count:
        return samples
    first = max(0, first - _ms_to_frames(padding_ms))
    return array("h", samples[first * settings.channels :])


def compact_internal_silence(
    samples: array,
    threshold: int = SILENCE_THRESHOLD,
    minimum_ms: int = LONG_SILENCE_MS,
    retained_ms: int = RETAINED_INTERNAL_SILENCE_MS,
) -> tuple[array, int]:
    """Shorten only long silent runs between voiced regions, never the ends."""
    frame_count = len(samples) // settings.channels
    minimum_frames = _ms_to_frames(minimum_ms)
    retained_frames = _ms_to_frames(retained_ms)
    compacted = array("h")
    cursor = 0
    removed_frames = 0
    while cursor < frame_count:
        if _frame_peak(samples, cursor) >= threshold:
            cursor += 1
            continue
        silence_start = cursor
        while cursor < frame_count and _frame_peak(samples, cursor) < threshold:
            cursor += 1
        silence_end = cursor
        silence_frames = silence_end - silence_start
        is_internal = silence_start > 0 and silence_end < frame_count
        if not is_internal or silence_frames < minimum_frames:
            continue
        compacted.extend(samples[: silence_start * settings.channels])
        compacted.extend(samples[silence_start * settings.channels : (silence_start + retained_frames) * settings.channels])
        samples = array("h", compacted + samples[silence_end * settings.channels :])
        removed_frames += silence_frames - retained_frames
        frame_count = len(samples) // settings.channels
        cursor = silence_start + retained_frames
        compacted = array("h")
    return samples, removed_frames


def rms_dbfs(samples: array) -> float:
    if not samples:
        return -90.0
    square_sum = sum(sample * sample for sample in samples)
    rms = math.sqrt(square_sum / len(samples))
    return 20 * math.log10(rms / SAMPLE_MAX) if rms > 0 else -90.0


def apply_gain(samples: array, gain_db: float) -> array:
    factor = 10 ** (gain_db / 20)
    return array("h", (_clamp_sample(int(round(sample * factor))) for sample in samples))


def audio_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return _frames_to_ms(handle.getnframes())


def read_internal_wav(path: Path) -> array:
    return _read_wav_samples(path)


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


def _replace_segment(destination: array, source: array, start_frame: int) -> None:
    offset = start_frame * settings.channels
    end = min(len(destination), offset + len(source))
    if offset >= end:
        return
    for index in range(offset, end):
        destination[index] = 0
    destination[offset:end] = source[: end - offset]


def _add_segment(destination: array, source: array, start_frame: int, gain: float) -> None:
    offset = start_frame * settings.channels
    end = min(len(destination), offset + len(source))
    for index in range(offset, end):
        destination[index] += int(round(source[index - offset] * gain))


def _atempo(source: Path, destination: Path, tempo: float) -> None:
    _run(["ffmpeg", "-y", "-i", str(source), "-filter:a", f"atempo={tempo:.6f}", str(destination)])


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
