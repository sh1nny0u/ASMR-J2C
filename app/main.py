from __future__ import annotations

from app.routes import router
import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_VERSION, RUN_LOG_PATH, STATIC_DIR
from app.jobs import store
from app.tts import TTSOptions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(RUN_LOG_PATH, encoding="utf-8"),
    ],
)
app = FastAPI(title="ASMR-J2C", version="0.1.0")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


@app.post("/api/jobs")
async def create_job(
    original_audio: UploadFile = File(...),
    lrc_file: UploadFile = File(...),
    reference_audio: UploadFile = File(...),
    output_format: str = Form("wav"),
    api_mode: str = Form("direct"),
    emo_control: str = Form("与音色参考音频相同"),
    emo_text: str = Form(""),
    emo_weight: float = Form(0.8),
    emo_random: bool = Form(False),
    emo_vec_1: float = Form(0.0),
    emo_vec_2: float = Form(0.0),
    emo_vec_3: float = Form(0.0),
    emo_vec_4: float = Form(0.0),
    emo_vec_5: float = Form(0.0),
    emo_vec_6: float = Form(0.0),
    emo_vec_7: float = Form(0.0),
    emo_vec_8: float = Form(0.0),
    top_k: int = Form(30),
    top_p: float = Form(0.8),
    temperature: float = Form(0.8),
    max_text_tokens_per_segment: int = Form(120),
    do_sample: bool = Form(True),
    num_beams: int = Form(3),
    repetition_penalty: float = Form(10.0),
    length_penalty: float = Form(0.0),
    max_mel_tokens: int = Form(1500),
    tts_url: str | None = Form(None),
    bilingual_mode: bool = Form(False),
    japanese_volume: float = Form(100.0),
    chinese_volume: float = Form(100.0),
) -> dict[str, object]:
    if output_format not in {"wav", "mp3"}:
        raise HTTPException(status_code=400, detail="output_format 只能是 wav 或 mp3。")
    subtitle_suffix = Path(lrc_file.filename or "").suffix.lower()
    if subtitle_suffix not in {".lrc", ".srt", ".vtt"}:
        raise HTTPException(status_code=400, detail="字幕格式仅支持 .lrc、.srt 或 .vtt。")
    if emo_control == "使用情感描述文本控制" and not emo_text.strip():
        raise HTTPException(status_code=400, detail="使用情感描述文本控制时，需要填写情感描述文本。")
    tts_options = TTSOptions(
        api_mode=api_mode if api_mode in {"queue", "direct"} else "queue",
        emo_control=emo_control,
        emo_text=emo_text,
        emo_weight=_clamp_float(emo_weight, 0.0, 1.6),
        emo_random=emo_random,
        emo_vec_1=_clamp_float(emo_vec_1, 0.0, 1.4),
        emo_vec_2=_clamp_float(emo_vec_2, 0.0, 1.4),
        emo_vec_3=_clamp_float(emo_vec_3, 0.0, 1.4),
        emo_vec_4=_clamp_float(emo_vec_4, 0.0, 1.4),
        emo_vec_5=_clamp_float(emo_vec_5, 0.0, 1.4),
        emo_vec_6=_clamp_float(emo_vec_6, 0.0, 1.4),
        emo_vec_7=_clamp_float(emo_vec_7, 0.0, 1.4),
        emo_vec_8=_clamp_float(emo_vec_8, 0.0, 1.4),
        top_k=int(_clamp_float(top_k, 0, 100)),
        top_p=_clamp_float(top_p, 0.0, 1.0),
        temperature=_clamp_float(temperature, 0.1, 2.0),
        max_text_tokens_per_segment=int(_clamp_float(max_text_tokens_per_segment, 20, 600)),
        do_sample=do_sample,
        num_beams=int(_clamp_float(num_beams, 1, 10)),
        repetition_penalty=_clamp_float(repetition_penalty, 0.0, 20.0),
        length_penalty=_clamp_float(length_penalty, -10.0, 10.0),
        max_mel_tokens=int(_clamp_float(max_mel_tokens, 50, 1815)),
    )
    if tts_options.emo_control == "使用情感向量控制":
        vector_sum = sum(
            [
                tts_options.emo_vec_1,
                tts_options.emo_vec_2,
                tts_options.emo_vec_3,
                tts_options.emo_vec_4,
                tts_options.emo_vec_5,
                tts_options.emo_vec_6,
                tts_options.emo_vec_7,
                tts_options.emo_vec_8,
            ]
        )
        if vector_sum <= 0:
            raise HTTPException(status_code=400, detail="使用情感向量控制时，至少需要设置一个大于 0 的情感向量。")
    job = await store.create_job(
        original_audio,
        lrc_file,
        reference_audio,
        output_format,
        tts_options,
        tts_url=tts_url,
        bilingual_mode=bilingual_mode,
        japanese_volume=_clamp_float(japanese_volume, 0.0, 200.0),
        chinese_volume=_clamp_float(chinese_volume, 0.0, 200.0),
    )
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, object]:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, object]:
    job = store.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/pause")
async def pause_job(job_id: str) -> dict[str, object]:
    job = store.pause(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/resume")
async def resume_job(job_id: str) -> dict[str, object]:
    job = store.resume(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str) -> FileResponse:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if job.status != "completed" or not job.output_path:
        raise HTTPException(status_code=409, detail="任务尚未完成。")
    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="输出文件不存在。")
    media_type = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media_type, filename=f"asmr-j2c-{job_id}{path.suffix}")


@app.post("/api/test-tts")
async def test_tts_connection(request: dict) -> dict[str, object]:
    import httpx
    url = request.get("tts_url", "").strip()
    if not url:
        return {"success": False, "error": "请提供 IndexTTS2 服务地址"}
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    url = url.rstrip("/")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{url}/config", timeout=5.0)
            if response.status_code == 200:
                return {"success": True, "message": f"连接成功: {url}"}
            else:
                return {"success": False, "error": f"HTTP {response.status_code}"}
    except httpx.ConnectError:
        return {"success": False, "error": "无法连接，请确认服务是否运行"}
    except httpx.TimeoutException:
        return {"success": False, "error": "连接超时 (5秒)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


app.include_router(router)

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


