from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import UploadFile

from app.audio import (
    compose_audio,
    convert_to_internal_wav,
    encode_output,
    prepare_tts_for_slot,
    probe_audio,
    read_internal_wav,
    schedule_segments,
    write_timing_metadata,
    write_binary_audio_to_wav,
)
from app.config import DATA_DIR
from app.lrc import parse_subtitle_file
from app.tts import IndexTTS2Client, TTSOptions


logger = logging.getLogger("asmr-j2c.jobs")
JobStatus = Literal["queued", "running", "paused", "completed", "failed", "cancelled"]


@dataclass
class Job:
    id: str
    status: JobStatus = "queued"
    progress: int = 0
    total: int = 0
    stage: str = "等待开始"
    current_text: str | None = None
    error: str | None = None
    output_format: str = "wav"
    output_path: str | None = None
    tts_options: dict[str, object] | None = None
    preview: dict[str, object] | None = None
    warnings: list[str] = field(default_factory=list)
    cancelled: bool = False
    pause_requested: bool = False
    tts_url: str | None = None
    bilingual_mode: bool = False
    japanese_volume: float = 100.0
    chinese_volume: float = 100.0
    timing: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["download_ready"] = self.status == "completed" and self.output_path is not None
        return payload


class JobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    async def create_job(
        self,
        original_audio: UploadFile,
        lrc_file: UploadFile,
        reference_audio: UploadFile,
        output_format: str,
        tts_options: TTSOptions,
        tts_url: str | None = None,
        bilingual_mode: bool = False,
        japanese_volume: float = 100.0,
        chinese_volume: float = 100.0,
    ) -> Job:
        job_id = uuid.uuid4().hex
        job_dir = DATA_DIR / "jobs" / job_id
        input_dir = job_dir / "inputs"
        input_dir.mkdir(parents=True, exist_ok=True)

        original_path = input_dir / f"original_{_safe_name(original_audio.filename, 'audio')}"
        lrc_path = input_dir / f"subtitle_{_safe_name(lrc_file.filename, 'lrc')}"
        reference_path = input_dir / f"reference_{_safe_name(reference_audio.filename, 'audio')}"

        await _save_upload(original_audio, original_path)
        await _save_upload(lrc_file, lrc_path)
        await _save_upload(reference_audio, reference_path)

        job = Job(
            id=job_id,
            output_format=output_format,
            tts_options=tts_options.__dict__,
            tts_url=tts_url,
            bilingual_mode=bilingual_mode,
            japanese_volume=japanese_volume,
            chinese_volume=chinese_volume,
        )
        self.jobs[job_id] = job
        task = asyncio.create_task(
            self._run_job(
                job,
                job_dir,
                original_path,
                lrc_path,
                reference_path,
                tts_options,
                tts_url,
            ),
            name=f"asmr-j2c-{job_id}",
        )
        self.tasks[job_id] = task
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        job.cancelled = True
        job.stage = "正在取消"
        task = self.tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        if job.status not in {"completed", "failed"}:
            job.status = "cancelled"
        return job

    def pause(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        if job.status == "running":
            job.pause_requested = True
            job.stage = "暂停中，当前句完成后停止"
        return job

    def resume(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        if job.status == "paused" or job.pause_requested:
            job.pause_requested = False
            job.status = "running"
            job.stage = "继续处理"
        return job

    async def _run_job(
        self,
        job: Job,
        job_dir: Path,
        original_path: Path,
        lrc_path: Path,
        reference_path: Path,
        tts_options: TTSOptions,
        tts_url: str | None = None,
    ) -> None:
        try:
            logger.info("job %s started", job.id)
            job.status = "running"
            work_dir = job_dir / "work"
            tts_dir = job_dir / "tts"
            work_dir.mkdir(parents=True, exist_ok=True)
            tts_dir.mkdir(parents=True, exist_ok=True)

            job.stage = "读取原音频"
            audio_info = await asyncio.to_thread(probe_audio, original_path)
            parsed = parse_subtitle_file(lrc_path, audio_duration_ms=audio_info.duration_ms)
            job.preview = parsed.to_dict()
            job.warnings.extend(parsed.warnings)
            job.total = len(parsed.lines)
            logger.info("job %s parsed %s subtitle lines, warnings=%s", job.id, job.total, len(parsed.warnings))
            if not parsed.lines:
                raise RuntimeError("字幕文件中没有可处理的有效字幕行。")

            job.stage = "转换原音频"
            logger.info("job %s converting original audio", job.id)
            original_wav = work_dir / "original_48k_stereo.wav"
            await asyncio.to_thread(convert_to_internal_wav, original_path, original_wav)
            original_samples = await asyncio.to_thread(read_internal_wav, original_wav)

            tts_client = IndexTTS2Client(base_url=tts_url) if tts_url else IndexTTS2Client()
            prepared_segments = []
            for line in parsed.lines:
                self._raise_if_cancelled(job)
                await self._wait_if_paused(job)
                job.stage = "调用 IndexTTS2"
                job.current_text = line.text
                job.progress = line.index
                logger.info("job %s synthesizing line %s/%s: %s", job.id, line.index + 1, job.total, line.text)

                cached_wav = tts_dir / f"{line.index:04d}.wav"
                if not cached_wav.exists():
                    audio_bytes = await tts_client.synthesize(line.text, reference_path, tts_options)
                    source_audio = tts_dir / f"{line.index:04d}.raw_response"
                    await asyncio.to_thread(write_binary_audio_to_wav, audio_bytes, source_audio, cached_wav)
                adjusted_wav = tts_dir / f"{line.index:04d}.adjusted.wav"
                prepared = await asyncio.to_thread(
                    prepare_tts_for_slot,
                    cached_wav,
                    original_samples,
                    line,
                    adjusted_wav,
                )
                prepared_segments.append(prepared)
                job.progress = line.index + 1
                await self._wait_if_paused(job)

            self._raise_if_cancelled(job)
            job.stage = "合成完整音频"
            logger.info("job %s composing final audio", job.id)
            scheduled_segments, schedule_warnings = schedule_segments(prepared_segments)
            job.timing = [segment.to_dict() for segment in scheduled_segments]
            await asyncio.to_thread(write_timing_metadata, tts_dir / "timing.json", scheduled_segments)
            composed_wav = work_dir / "composed.wav"
            compose_warnings = await asyncio.to_thread(
                compose_audio,
                original_wav,
                scheduled_segments,
                composed_wav,
                job.bilingual_mode,
                job.japanese_volume,
                job.chinese_volume,
            )
            job.warnings.append("已保留每句原始 TTS 响应、转换 WAV 和 timing.json，便于排查源 TTS 是否漏字。")
            job.warnings.extend(schedule_warnings)
            job.warnings.extend(compose_warnings)

            self._raise_if_cancelled(job)
            job.stage = "编码输出"
            logger.info("job %s encoding output format=%s", job.id, job.output_format)
            suffix = "mp3" if job.output_format == "mp3" else "wav"
            output_path = job_dir / f"output.{suffix}"
            await asyncio.to_thread(encode_output, composed_wav, output_path, suffix)

            job.output_path = str(output_path)
            job.current_text = None
            job.stage = "完成"
            job.status = "completed"
            job.progress = job.total
            logger.info("job %s completed: %s", job.id, output_path)
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.stage = "已取消"
            logger.info("job %s cancelled", job.id)
        except Exception as exc:
            job.status = "failed"
            job.stage = "失败"
            job.error = str(exc)
            logger.exception("job %s failed: %s", job.id, exc)

    @staticmethod
    def _raise_if_cancelled(job: Job) -> None:
        if job.cancelled:
            raise asyncio.CancelledError

    @staticmethod
    async def _wait_if_paused(job: Job) -> None:
        if job.pause_requested:
            job.status = "paused"
            job.stage = "已暂停"
            logger.info("job %s paused", job.id)
        while job.pause_requested:
            if job.cancelled:
                raise asyncio.CancelledError
            await asyncio.sleep(0.5)
        if job.status == "paused":
            job.status = "running"
            job.stage = "继续处理"
            logger.info("job %s resumed", job.id)


async def _save_upload(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    await upload.close()


def _safe_name(filename: str | None, fallback: str) -> str:
    if not filename:
        return fallback
    name = Path(filename).name.replace("/", "_").replace("\\", "_")
    return name or fallback


store = JobStore()
