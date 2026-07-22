from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
STATIC_DIR = ROOT_DIR / "static"
RUN_LOG_PATH = ROOT_DIR / "runtime.log"
APP_VERSION = "2026-05-03-tts-fix"


@dataclass(frozen=True)
class Settings:
    indextts2_base_url: str = os.getenv("INDEXTTS2_BASE_URL", "http://127.0.0.1:7860")
    indextts2_api_url: str = os.getenv("INDEXTTS2_API_URL", "")
    timeout_seconds: float = float(os.getenv("INDEXTTS2_TIMEOUT", "180"))
    retries: int = int(os.getenv("INDEXTTS2_RETRIES", "1"))
    emo_control: str = os.getenv("INDEXTTS2_EMO_CONTROL", "与音色参考音频相同")
    emo_text: str = os.getenv("INDEXTTS2_EMO_TEXT", "")
    emo_random: bool = os.getenv("INDEXTTS2_EMO_RANDOM", "false").lower() == "true"
    top_k: int = int(os.getenv("INDEXTTS2_TOP_K", "30"))
    top_p: float = float(os.getenv("INDEXTTS2_TOP_P", "0.8"))
    temperature: float = float(os.getenv("INDEXTTS2_TEMPERATURE", "0.8"))
    max_text_tokens_per_segment: int = int(os.getenv("INDEXTTS2_MAX_TEXT_TOKENS", "120"))
    sample_rate: int = int(os.getenv("ASMR_J2C_SAMPLE_RATE", "48000"))
    channels: int = int(os.getenv("ASMR_J2C_CHANNELS", "2"))
    target_lufs_fallback: float = float(os.getenv("ASMR_J2C_TARGET_DBFS", "-18"))


settings = Settings()
