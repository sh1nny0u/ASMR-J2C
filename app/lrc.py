from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable


TIME_TOKEN_RE = re.compile(r"\[(?P<time>\d{1,3}:\d{2}(?:[.:]\d{1,3})?)\]")
RANGE_RE = re.compile(
    r"^\s*(?:\[)?(?P<start>\d{1,3}:\d{2}(?:[.:]\d{1,3})?)(?:\])?\s*"
    r"(?:-|-->|~|,|\s+)\s*"
    r"(?:\[)?(?P<end>\d{1,3}:\d{2}(?:[.:]\d{1,3})?)(?:\])?\s*"
    r"(?P<text>.+?)\s*$"
)


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
class LrcParseResult:
    lines: list[SubtitleLine]
    warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "lines": [line.to_dict() for line in self.lines],
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class _RawLine:
    source_line: int
    start_ms: int
    end_ms: int | None
    text: str


def parse_time_ms(value: str) -> int:
    minute_text, rest = value.strip().split(":", 1)
    if "." in rest:
        second_text, fraction = rest.split(".", 1)
    elif ":" in rest:
        second_text, fraction = rest.split(":", 1)
    else:
        second_text, fraction = rest, ""

    minutes = int(minute_text)
    seconds = int(second_text)
    if seconds >= 60:
        raise ValueError(f"Invalid seconds in time token: {value}")

    fraction = (fraction + "000")[:3]
    millis = int(fraction) if fraction else 0
    return minutes * 60_000 + seconds * 1_000 + millis


def parse_lrc(content: str, audio_duration_ms: int | None = None, max_last_line_ms: int = 6_000) -> LrcParseResult:
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

            text = range_match.group("text").strip()
            _append_raw(raw_lines, warnings, line_number, start_ms, end_ms, text)
            continue

        tokens = list(TIME_TOKEN_RE.finditer(line))
        if not tokens:
            warnings.append(f"第 {line_number} 行没有可识别时间戳，已跳过。")
            continue

        text_start = tokens[-1].end()
        text = line[text_start:].strip()
        if not text:
            warnings.append(f"第 {line_number} 行没有字幕文本，已跳过。")
            continue

        try:
            times = [parse_time_ms(match.group("time")) for match in tokens]
        except ValueError as exc:
            warnings.append(f"第 {line_number} 行时间格式无效：{exc}")
            continue

        if len(times) >= 2:
            _append_raw(raw_lines, warnings, line_number, times[0], times[1], text)
        else:
            _append_raw(raw_lines, warnings, line_number, times[0], None, text)

    raw_lines.sort(key=lambda item: (item.start_ms, item.source_line))
    return _finalize_lines(raw_lines, warnings, audio_duration_ms, max_last_line_ms)


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
    raw_lines.append(_RawLine(source_line=source_line, start_ms=start_ms, end_ms=end_ms, text=text))


def _finalize_lines(
    raw_lines: Iterable[_RawLine],
    warnings: list[str],
    audio_duration_ms: int | None,
    max_last_line_ms: int,
) -> LrcParseResult:
    finalized: list[SubtitleLine] = []
    previous_end = -1
    seen_starts: set[int] = set()
    raw_list = list(raw_lines)

    for index, item in enumerate(raw_list):
        if item.start_ms in seen_starts:
            warnings.append(f"第 {item.source_line} 行开始时间重复，已跳过。")
            continue
        seen_starts.add(item.start_ms)

        next_start = _next_distinct_start(raw_list, index)
        inferred_end = item.end_ms
        if inferred_end is None:
            if next_start is not None:
                inferred_end = next_start
            elif audio_duration_ms is not None:
                inferred_end = min(audio_duration_ms, item.start_ms + max_last_line_ms)
            else:
                inferred_end = item.start_ms + max_last_line_ms

        if audio_duration_ms is not None:
            if item.start_ms >= audio_duration_ms:
                warnings.append(f"第 {item.source_line} 行开始时间超过音频长度，已跳过。")
                continue
            inferred_end = min(inferred_end, audio_duration_ms)

        if inferred_end <= item.start_ms:
            warnings.append(f"第 {item.source_line} 行有效时长为 0，已跳过。")
            continue
        if item.start_ms < previous_end:
            warnings.append(f"第 {item.source_line} 行和上一句时间重叠，已跳过。")
            continue

        finalized.append(
            SubtitleLine(
                index=len(finalized),
                source_line=item.source_line,
                start_ms=item.start_ms,
                end_ms=inferred_end,
                text=item.text,
            )
        )
        previous_end = inferred_end

    return LrcParseResult(lines=finalized, warnings=warnings)


def _next_distinct_start(raw_lines: list[_RawLine], index: int) -> int | None:
    current = raw_lines[index].start_ms
    for item in raw_lines[index + 1 :]:
        if item.start_ms != current:
            return item.start_ms
    return None
