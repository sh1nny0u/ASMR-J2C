from __future__ import annotations

import asyncio
import logging
import base64
from pathlib import Path
import httpx
from dataclasses import dataclass
from typing import Any, Optional, List
import urllib.parse

import gradio_client as grc

from app.config import settings

logger = logging.getLogger("asmr-j2c.tts")

@dataclass(frozen=True)
class TTSOptions:
    api_mode: str = "queue"
    emo_control: str = "与音色参考音频相同"
    emo_text: str = ""
    emo_weight: float = 0.8
    emo_random: bool = False
    emo_vec_1: float = 0.0
    emo_vec_2: float = 0.0
    emo_vec_3: float = 0.0
    emo_vec_4: float = 0.0
    emo_vec_5: float = 0.0
    emo_vec_6: float = 0.0
    emo_vec_7: float = 0.0
    emo_vec_8: float = 0.0
    top_k: int = 30
    top_p: float = 0.8
    temperature: float = 0.8
    max_text_tokens_per_segment: int = 120
    do_sample: bool = True
    num_beams: int = 3
    repetition_penalty: float = 10.0
    length_penalty: float = 0.0
    max_mel_tokens: int = 1500


class IndexTTS2Client:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.indextts2_base_url).rstrip("/")
        self.client = grc.Client(self.base_url)
        self.api_name = "/gen_single"
        logger.info("Using IndexTTS2 API endpoint: %s", self.api_name)

    async def synthesize(self, text: str, reference_audio: Path, options: TTSOptions | None = None) -> bytes:
        options = options or TTSOptions()
        last_error: Exception | None = None
        for attempt in range(settings.retries + 1):
            try:
                return await self._synthesize_once(text, reference_audio, options)
            except Exception as exc:
                last_error = exc
                logger.warning("IndexTTS2 attempt failed (%s/%s): %s", attempt + 1, settings.retries + 1, exc)
                if attempt >= settings.retries:
                    break
        raise RuntimeError(f"IndexTTS2 生成失败：{last_error}") from last_error

    async def _synthesize_once(self, text: str, ref_audio: Path, options: TTSOptions) -> bytes:
        def _file_dict(p: Path) -> dict:
            return {"path": str(p), "meta": {"_type": "gradio.FileData"}}

        param_list: List[Any] = [
            options.emo_control,
            _file_dict(ref_audio),
            text,
            None,
            options.emo_weight,
            options.emo_vec_1, options.emo_vec_2, options.emo_vec_3, options.emo_vec_4,
            options.emo_vec_5, options.emo_vec_6, options.emo_vec_7, options.emo_vec_8,
            options.emo_text,
            options.emo_random,
            options.max_text_tokens_per_segment,
            options.do_sample,
            options.top_p,
            options.top_k,
            options.temperature,
            options.length_penalty,
            options.num_beams,
            options.repetition_penalty,
            options.max_mel_tokens,
        ]
        try:
            result = await asyncio.to_thread(
                self.client.predict,
                *param_list,
                api_name=self.api_name,
            )
            logger.info("Predict result type: %s, value: %s", type(result), repr(result)[:200])
            # 提取音频文件路径字符串
            audio_path = self._extract_path_string(result)
            if not audio_path:
                raise RuntimeError(f"No audio file path returned from IndexTTS2. Result: {result}")
            logger.info("Audio file path from IndexTTS2: %s", audio_path)
            # 通过 HTTP 下载音频文件
            return await self._download_audio_file(audio_path)
        except Exception as e:
            logger.exception("gradio_client predict failed")
            raise RuntimeError(f"IndexTTS2 predict error: {e}") from e

    def _extract_path_string(self, obj: Any) -> Optional[str]:
        """从 gradio_client 返回的对象中提取文件路径字符串"""
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            # Gradio 有时返回 {'visible': True, 'value': 'path', '__type__': 'update'}
            if "value" in obj and isinstance(obj["value"], str):
                return obj["value"]
            # 其他常见键
            for key in ("path", "file", "name", "url"):
                if key in obj and isinstance(obj[key], str):
                    return obj[key]
            if "data" in obj:
                if isinstance(obj["data"], str):
                    return obj["data"]
                if isinstance(obj["data"], dict) and "value" in obj["data"]:
                    return obj["data"]["value"]
            return None
        if isinstance(obj, (list, tuple)) and len(obj) > 0:
            return self._extract_path_string(obj[0])
        if hasattr(obj, '__str__') and str(obj):
            s = str(obj)
            if s and len(s) > 5:
                return s
        if hasattr(obj, 'name') and isinstance(obj.name, str):
            return obj.name
        return None

    async def _download_audio_file(self, path_or_url: str) -> bytes:
        """通过 HTTP 从 Gradio 服务器下载音频文件"""
        # 如果已经是完整 URL，直接使用
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        else:
            # 构造 Gradio 文件端点 URL
            # 将 Windows 反斜杠转换为正斜杠，并进行 URL 编码
            rel_path = path_or_url.replace("\\", "/")
            encoded = urllib.parse.quote(rel_path, safe="/")
            url = f"{self.base_url}/gradio_api/file={encoded}"
        logger.info("Downloading audio from %s", url)
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content
            if len(content) < 100:
                raise RuntimeError(f"Downloaded file too small ({len(content)} bytes)")
            logger.info("Downloaded %d bytes", len(content))
            return content
