from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


LRC_TIME_TOKEN_RE = re.compile(r"\[(?P<time>\d{1,3}:\d{2}(?:[.:]\d{1,3})?)\]")
RANGE_RE = re.compile(
    r"^\s*(?:\[)?(?P<start>\d{1,3}:\d{2}(?:[.:]\d{1,3})?)(?:\])?\s*"
    r"(?:-|-->|~|,|\s+)\s*"
    r"(?:\[)?(?P<end>\d{1,3}:\d{2}(?:[.:]\d{1,3})?)(?:\])?\s*"
    r"(?P<text>.+?)\s*$"
)
CUE_TIMING_RE = re.compile(
    r"^\s*(?P<start>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\s*-->\s*"
    r"(?P<end>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)(?:\s+.*)?$"
)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SubtitleLine:
    index: int
    source_line: int
    start_ms: int
    end_ms: int
    text: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SubtitleParseResult:
    lines: list[SubtitleLine]
    warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "lines": [line.to_dict() for line in self.lines],
            "warnings": self.warnings,
        }


# Kept for callers that imported the old name.
LrcParseResult = SubtitleParseResult


@dataclass(frozen=True)
class _RawLine:
    source_line: int
    start_ms: int
    end_ms: int | None
    text: str


def decode_subtitle_bytes(content: bytes) -> str:
    """Decode common subtitle encodings without silently replacing invalid text."""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return content.decode("utf-16")
        except UnicodeDecodeError:
            pass
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # UTF-16 without a BOM is uncommon, but has enough NUL bytes to distinguish it from GB18030.
    if content and content.count(b"\x00") * 4 >= len(content):
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
    try:
        return content.decode("gb18030")
    except UnicodeDecodeError:
        pass
    raise ValueError("字幕文件编码无法识别，请保存为 UTF-8、UTF-16 或 GB18030 后重试。")


def parse_time_ms(value: str) -> int:
    normalized = value.strip().replace(",", ".")
    if "." in normalized:
        whole, fraction = normalized.split(".", 1)
    else:
        whole, fraction = normalized, ""

    parts = whole.split(":")
    if len(parts) == 2:
        hours = 0
        minutes_text, seconds_text = parts
    elif len(parts) == 3:
        hours_text, minutes_text, seconds_text = parts
        hours = int(hours_text)
    else:
        raise ValueError(f"无效时间戳：{value}")

    minutes = int(minutes_text)
    seconds = int(seconds_text)
    if minutes >= 60 or seconds >= 60 or hours < 0:
        raise ValueError(f"无效时间戳：{value}")
    millis = int((fraction + "000")[:3]) if fraction else 0
    return ((hours * 60 + minutes) * 60 + seconds) * 1_000 + millis


def parse_subtitle_file(
    path: Path,
    audio_duration_ms: int | None = None,
    max_last_line_ms: int = 6_000,
) -> SubtitleParseResult:
    extension = path.suffix.lower()
    if extension not in {".lrc", ".srt", ".vtt"}:
        raise ValueError("字幕格式仅支持 .lrc、.srt 或 .vtt。")
    return parse_subtitle(
        decode_subtitle_bytes(path.read_bytes()),
        extension=extension,
        audio_duration_ms=audio_duration_ms,
        max_last_line_ms=max_last_line_ms,
    )


def parse_subtitle(
    content: str,
    extension: str,
    audio_duration_ms: int | None = None,
    max_last_line_ms: int = 6_000,
) -> SubtitleParseResult:
    extension = extension.lower()
    if extension == ".lrc":
        return parse_lrc(content, audio_duration_ms, max_last_line_ms)
    if extension in {".srt", ".vtt"}:
        return _parse_cue_subtitles(content, extension, audio_duration_ms, max_last_line_ms)
    raise ValueError("字幕格式仅支持 .lrc、.srt 或 .vtt。")


def parse_lrc(
    content: str,
    audio_duration_ms: int | None = None,
    max_last_line_ms: int = 6_000,
) -> SubtitleParseResult:
    raw_lines: list[_RawLine] = []
    warnings: list[str] = []

    for line_number, original in enumerate(content.splitlines(), start=1):
        line = original.strip()
        if not line:
            continue

        range_match = RANGE_RE.match(line)
        if range_match:
            try:
                start_ms = parse_time_ms(range_match.group("start"))
                end_ms = parse_time_ms(range_match.group("end"))
            except ValueError as exc:
                warnings.append(f"第 {line_number} 行时间格式无效：{exc}")
                continue
            _append_raw(raw_lines, warnings, line_number, start_ms, end_ms, range_match.group("text").strip())
            continue

        tokens = list(LRC_TIME_TOKEN_RE.finditer(line))
        if not tokens:
            warnings.append(f"第 {line_number} 行没有可识别时间戳，已跳过。")
            continue
        text = line[tokens[-1].end() :].strip()
        if not text:
            warnings.append(f"第 {line_number} 行没有字幕文本，已跳过。")
            continue
        try:
            times = [parse_time_ms(match.group("time")) for match in tokens]
        except ValueError as exc:
            warnings.append(f"第 {line_number} 行时间格式无效：{exc}")
            continue
        _append_raw(raw_lines, warnings, line_number, times[0], times[1] if len(times) >= 2 else None, text)

    return _finalize(raw_lines, warnings, audio_duration_ms, max_last_line_ms)


def _parse_cue_subtitles(
    content: str,
    extension: str,
    audio_duration_ms: int | None,
    max_last_line_ms: int,
) -> SubtitleParseResult:
    lines = content.lstrip("\ufeff").splitlines()
    raw_lines: list[_RawLine] = []
    warnings: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if extension == ".vtt" and (line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION"))):
            index += 1
            while index < len(lines) and lines[index].strip():
                index += 1
            continue

        timing_line_number = index + 1
        timing_match = CUE_TIMING_RE.match(line)
        if not timing_match and index + 1 < len(lines):
            timing_match = CUE_TIMING_RE.match(lines[index + 1].strip())
            if timing_match:
                index += 1
                timing_line_number = index + 1
        if not timing_match:
            warnings.append(f"第 {index + 1} 行不是有效字幕时间轴，已跳过。")
            index += 1
            continue

        try:
            start_ms = parse_time_ms(timing_match.group("start"))
            end_ms = parse_time_ms(timing_match.group("end"))
        except ValueError as exc:
            warnings.append(f"第 {timing_line_number} 行时间格式无效：{exc}")
            index += 1
            continue

        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = TAG_RE.sub("", " ".join(text_lines)).strip()
        _append_raw(raw_lines, warnings, timing_line_number, start_ms, end_ms, text)

    return _finalize(raw_lines, warnings, audio_duration_ms, max_last_line_ms)


def _append_raw(
    raw_lines: list[_RawLine],
    warnings: list[str],
    source_line: int,
    start_ms: int,
    end_ms: int | None,
    text: str,
) -> None:
    if not text:
        warnings.append(f"第 {source_line} 行没有字幕文本，已跳过。")
        return
    if end_ms is not None and end_ms <= start_ms:
        warnings.append(f"第 {source_line} 行结束时间不晚于开始时间，已跳过。")
        return
    raw_lines.append(_RawLine(source_line, start_ms, end_ms, text))


def _finalize(
    raw_lines: Iterable[_RawLine],
    warnings: list[str],
    audio_duration_ms: int | None,
    max_last_line_ms: int,
) -> SubtitleParseResult:
    finalized: list[SubtitleLine] = []
    previous_end = -1
    seen_starts: set[int] = set()
    ordered = sorted(raw_lines, key=lambda item: (item.start_ms, item.source_line))

    for index, item in enumerate(ordered):
        if item.start_ms in seen_starts:
            warnings.append(f"第 {item.source_line} 行开始时间重复，已跳过。")
            continue
        seen_starts.add(item.start_ms)
        end_ms = item.end_ms
        if end_ms is None:
            next_start = next((next_item.start_ms for next_item in ordered[index + 1 :] if next_item.start_ms != item.start_ms), None)
            end_ms = next_start if next_start is not None else item.start_ms + max_last_line_ms
        if audio_duration_ms is not None:
            if item.start_ms >= audio_duration_ms:
                warnings.append(f"第 {item.source_line} 行开始时间超过音频长度，已跳过。")
                continue
            end_ms = min(end_ms, audio_duration_ms)
        if end_ms <= item.start_ms:
            warnings.append(f"第 {item.source_line} 行有效时长为 0，已跳过。")
            continue
        if item.start_ms < previous_end:
            warnings.append(f"第 {item.source_line} 行和上一句时间重叠，已跳过。")
            continue
        finalized.append(SubtitleLine(len(finalized), item.source_line, item.start_ms, end_ms, item.text))
        previous_end = end_ms

    return SubtitleParseResult(finalized, warnings)
