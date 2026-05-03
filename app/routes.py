from fastapi import APIRouter, File, Form, UploadFile
from app.jobs import store
from app.config import settings
from app.tts import TTSOptions

router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}

@router.post("/jobs")
async def create_job(
    original_audio: UploadFile = File(...),
    lrc_file: UploadFile = File(...),
    reference_audio: UploadFile = File(...),
    output_format: str = Form("wav"),
    api_mode: str = Form("queue"),
    emo_control: str = Form(...),
    emo_text: str = Form(""),
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
    emo_weight: float = Form(0.8),
    tts_url: str | None = Form(None),
):
    tts_options = TTSOptions(
        api_mode=api_mode,
        emo_control=emo_control,
        emo_text=emo_text,
        emo_random=emo_random,
        emo_vec_1=emo_vec_1,
        emo_vec_2=emo_vec_2,
        emo_vec_3=emo_vec_3,
        emo_vec_4=emo_vec_4,
        emo_vec_5=emo_vec_5,
        emo_vec_6=emo_vec_6,
        emo_vec_7=emo_vec_7,
        emo_vec_8=emo_vec_8,
        top_k=top_k,
        top_p=top_p,
        temperature=temperature,
        max_text_tokens_per_segment=max_text_tokens_per_segment,
        do_sample=do_sample,
        num_beams=num_beams,
        repetition_penalty=repetition_penalty,
        length_penalty=length_penalty,
        max_mel_tokens=max_mel_tokens,
        emo_weight=emo_weight,
    )
    job = await store.create_job(original_audio, lrc_file, reference_audio, output_format, tts_options, tts_url)
    return {"id": job.id}
