from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx

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
        self.api_url = settings.indextts2_api_url.strip()
        self._resolved_api_url: str | None = None
        self._event_url_base: str | None = None
        self._api_prefix: str = "/gradio_api"
        self._saved_timbre_name: str = ""

    async def synthesize(self, text: str, reference_audio: Path, options: TTSOptions | None = None) -> bytes:
        options = options or TTSOptions(
            emo_control=settings.emo_control,
            emo_text=settings.emo_text,
            emo_random=settings.emo_random,
            top_k=settings.top_k,
            top_p=settings.top_p,
            temperature=settings.temperature,
            max_text_tokens_per_segment=settings.max_text_tokens_per_segment,
        )
        last_error: Exception | None = None
        for attempt in range(settings.retries + 1):
            try:
                return await self._synthesize_once(text, reference_audio, options)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "IndexTTS2 attempt failed, attempt=%s/%s: %s",
                    attempt + 1,
                    settings.retries + 1,
                    exc,
                )
                if attempt >= settings.retries:
                    break
        raise RuntimeError(f"IndexTTS2 生成失败：{last_error}") from last_error

    async def _synthesize_once(
        self,
        text: str,
        reference_audio: Path,
        options: TTSOptions,
    ) -> bytes:
        timeout = httpx.Timeout(settings.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            if options.api_mode == "queue":
                return await self._synthesize_via_queue(client, text, reference_audio, options)
            return await self._synthesize_via_direct_tts(client, text, reference_audio, options)
        raise RuntimeError("IndexTTS2 返回 JSON，但没有 data[0].path/audio_base64/url/path 字段。")

    async def _synthesize_via_direct_tts(
        self,
        client: httpx.AsyncClient,
        text: str,
        reference_audio: Path,
        options: TTSOptions,
    ) -> bytes:
        response = await client.post(
            await self._resolve_api_url(client),
            json={"data": await self._build_direct_payload(client, text, reference_audio, options)},
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response.content

        payload = response.json()
        if "event_id" in payload:
            return await self._read_gradio_event_audio(client, await self._resolve_api_url(client), str(payload["event_id"]))
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        audio = _extract_audio_from_payload(payload)
        if audio is not None:
            return audio
        if "url" in payload:
            url = urljoin(self.base_url + "/", str(payload["url"]))
            audio_response = await client.get(url)
            audio_response.raise_for_status()
            return audio_response.content
        raise RuntimeError("IndexTTS2 直接 TTS 接口没有返回可识别音频。")

    async def _synthesize_via_queue(
        self,
        client: httpx.AsyncClient,
        text: str,
        reference_audio: Path,
        options: TTSOptions,
    ) -> bytes:
        await self._resolve_api_url(client)
        add_url = f"{self.base_url}{self._api_prefix}/call/add_and_trigger_processing"
        process_url = f"{self.base_url}{self._api_prefix}/call/process_tasks_triggered_by_event"
        prompt_audio = await self._upload_file(client, reference_audio)
        payload = self._build_queue_payload(text, prompt_audio, options)

        response = await client.post(add_url, json={"data": payload})
        response.raise_for_status()
        add_payload = response.json()
        if "event_id" not in add_payload:
            raise RuntimeError(f"IndexTTS2 队列添加任务失败：{add_payload}")

        add_result = await self._read_gradio_event_payload(client, add_url, str(add_payload["event_id"]))
        trigger_value = _extract_last_number(add_result)
        if trigger_value is None:
            raise RuntimeError(f"IndexTTS2 队列没有返回触发值：{add_result}")

        response = await client.post(process_url, json={"data": [trigger_value]})
        response.raise_for_status()
        process_payload = response.json()
        if "event_id" not in process_payload:
            raise RuntimeError(f"IndexTTS2 队列处理任务失败：{process_payload}")
        return await self._read_gradio_event_audio(client, process_url, str(process_payload["event_id"]))

    async def _resolve_api_url(self, client: httpx.AsyncClient) -> str:
        if self.api_url:
            self._event_url_base = self.api_url
            return self.api_url
        if self._resolved_api_url:
            return self._resolved_api_url

        config_url = f"{self.base_url}/config"
        try:
            response = await client.get(config_url)
            response.raise_for_status()
            config = response.json()
            api_prefix = str(config.get("api_prefix") or "/gradio_api").rstrip("/")
            self._saved_timbre_name = _extract_saved_timbre_name(config)
        except Exception:
            api_prefix = "/gradio_api"

        self._api_prefix = api_prefix
        self._resolved_api_url = f"{self.base_url}{api_prefix}/call/tts"
        self._event_url_base = self._resolved_api_url
        logger.info("IndexTTS2 endpoint resolved: %s", self._resolved_api_url)
        return self._resolved_api_url

    async def _read_gradio_event_audio(self, client: httpx.AsyncClient, event_base: str, event_id: str) -> bytes:
        event_payload = await self._read_gradio_event_payload(client, event_base, event_id)
        audio = _extract_audio_from_payload(event_payload)
        if audio is not None:
            return audio
        raise RuntimeError(f"IndexTTS2 Gradio event ended without an audio path: {event_payload}")

    async def _read_gradio_event_payload(self, client: httpx.AsyncClient, event_base: str, event_id: str) -> object:
        event_url = f"{event_base.rstrip('/')}/{event_id}"
        last_payload: object = None
        async with client.stream("GET", event_url) as response:
            response.raise_for_status()
            event_name = ""
            async for raw_line in response.aiter_lines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                    continue
                if not line.startswith("data:"):
                    continue

                data_text = line.split(":", 1)[1].strip()
                if event_name == "error":
                    raise RuntimeError(data_text or "IndexTTS2 Gradio event returned error.")
                if data_text in {"null", "None"}:
                    continue

                try:
                    event_payload = json.loads(data_text)
                except json.JSONDecodeError:
                    continue

                last_payload = event_payload
                audio = _extract_audio_from_payload(event_payload)
                if audio is not None:
                    return event_payload

        if last_payload is not None:
            return last_payload
        raise RuntimeError("IndexTTS2 Gradio event ended without data.")

    async def _build_direct_payload(
        self,
        client: httpx.AsyncClient,
        text: str,
        reference_audio: Path,
        options: TTSOptions,
    ) -> list[object]:
        prompt_audio = await self._upload_file(client, reference_audio)
        payload = [
            prompt_audio,
            text,
            options.emo_control,
            None,
            options.emo_text,
            options.emo_random,
            options.emo_vec_1,
            options.emo_vec_2,
            options.emo_vec_3,
            options.emo_vec_4,
            options.emo_vec_5,
            options.emo_vec_6,
            options.emo_vec_7,
            options.emo_vec_8,
            options.top_k,
            options.top_p,
            options.temperature,
            options.max_text_tokens_per_segment,
        ]
        logger.info(
            "IndexTTS2 direct params: emo_control=%r, emo_text=%r, emo_random=%s, "
            "vec=[%.2f, %.2f, %.2f, %.2f, %.2f, %.2f, %.2f, %.2f], "
            "top_k=%s, top_p=%.3f, temperature=%.3f, max_text_tokens=%s",
            options.emo_control,
            options.emo_text,
            options.emo_random,
            options.emo_vec_1,
            options.emo_vec_2,
            options.emo_vec_3,
            options.emo_vec_4,
            options.emo_vec_5,
            options.emo_vec_6,
            options.emo_vec_7,
            options.emo_vec_8,
            options.top_k,
            options.top_p,
            options.temperature,
            options.max_text_tokens_per_segment,
        )
        return payload

    def _build_queue_payload(self, text: str, prompt_audio: dict[str, object], options: TTSOptions) -> list[object]:
        payload = [
            "上传新音色",
            prompt_audio,
            self._saved_timbre_name,
            text,
            options.emo_control,
            None,
            options.emo_weight,
            options.emo_random,
            options.emo_vec_1,
            options.emo_vec_2,
            options.emo_vec_3,
            options.emo_vec_4,
            options.emo_vec_5,
            options.emo_vec_6,
            options.emo_vec_7,
            options.emo_vec_8,
            options.emo_text,
            options.max_text_tokens_per_segment,
            options.do_sample,
            options.top_p,
            options.top_k,
            options.temperature,
            options.length_penalty,
            options.num_beams,
            options.repetition_penalty,
            options.max_mel_tokens,
            0,
        ]
        logger.info(
            "IndexTTS2 queue params: saved_timbre=%r, emo_control=%r, emo_weight=%.2f, emo_text=%r, emo_random=%s, "
            "vec=[%.2f, %.2f, %.2f, %.2f, %.2f, %.2f, %.2f, %.2f], "
            "do_sample=%s, top_k=%s, top_p=%.3f, temperature=%.3f, num_beams=%s, "
            "repetition_penalty=%.3f, length_penalty=%.3f, max_mel_tokens=%s, max_text_tokens=%s",
            self._saved_timbre_name,
            options.emo_control,
            options.emo_weight,
            options.emo_text,
            options.emo_random,
            options.emo_vec_1,
            options.emo_vec_2,
            options.emo_vec_3,
            options.emo_vec_4,
            options.emo_vec_5,
            options.emo_vec_6,
            options.emo_vec_7,
            options.emo_vec_8,
            options.do_sample,
            options.top_k,
            options.top_p,
            options.temperature,
            options.num_beams,
            options.repetition_penalty,
            options.length_penalty,
            options.max_mel_tokens,
            options.max_text_tokens_per_segment,
        )
        return payload

    async def _upload_file(self, client: httpx.AsyncClient, path: Path) -> dict[str, object]:
        await self._resolve_api_url(client)
        upload_url = f"{self.base_url}{self._api_prefix}/upload"
        logger.info("uploading reference audio to Gradio: %s", path)
        with path.open("rb") as handle:
            response = await client.post(
                upload_url,
                files={"files": (path.name, handle, "application/octet-stream")},
            )
        response.raise_for_status()
        payload = response.json()
        uploaded_path = _extract_uploaded_path(payload)
        if not uploaded_path:
            raise RuntimeError(f"Gradio 上传参考音频失败，返回值无法识别：{payload}")
        logger.info("reference audio uploaded to Gradio cache: %s", uploaded_path)
        return {
            "path": uploaded_path,
            "orig_name": path.name,
            "meta": {"_type": "gradio.FileData"},
        }


def _read_local_audio_path(value: object) -> bytes:
    """读取本地音频文件，只接受普通文件（不是目录）。"""
    path = Path(str(value))
    if not path.is_file():
        raise RuntimeError(f"IndexTTS2 返回的音频路径不是有效文件：{path}")
    try:
        return path.read_bytes()
    except PermissionError as e:
        raise RuntimeError(f"读取音频文件失败，权限错误：{path}") from e


def _extract_audio_from_payload(payload: object) -> bytes | None:
    """递归地从 Gradio payload 中提取音频数据，支持嵌套、value.path 等结构。"""
    if isinstance(payload, dict):
        # 优先直接取 path
        if "path" in payload:
            try:
                return _read_local_audio_path(payload["path"])
            except Exception:
                pass
        # 处理 {"value": {"path": ...}} 这种模式
        if "value" in payload:
            audio = _extract_audio_from_payload(payload["value"])
            if audio is not None:
                return audio
        # 处理 audio_base64
        if "audio_base64" in payload:
            return base64.b64decode(str(payload["audio_base64"]))
        # 递归 data 字段
        if "data" in payload:
            return _extract_audio_from_payload(payload["data"])
        # 遍历所有值
        for value in payload.values():
            audio = _extract_audio_from_payload(value)
            if audio is not None:
                return audio
        return None
    if isinstance(payload, list):
        for item in payload:
            audio = _extract_audio_from_payload(item)
            if audio is not None:
                return audio
        return None
    if isinstance(payload, str):
        path = Path(payload)
        if path.is_file():
            try:
                return path.read_bytes()
            except Exception:
                pass
    return None

def _extract_saved_timbre_name(config: object) -> str:
    if not isinstance(config, dict):
        return ""

    components = {
        component.get("id"): component
        for component in config.get("components", [])
        if isinstance(component, dict)
    }
    dependencies = config.get("dependencies", [])
    if not isinstance(dependencies, list):
        return ""

    for dependency in dependencies:
        if not isinstance(dependency, dict) or dependency.get("api_name") != "add_and_trigger_processing":
            continue
        inputs = dependency.get("inputs", [])
        if not isinstance(inputs, list) or len(inputs) < 3:
            return ""
        saved_timbre_component = components.get(inputs[2])
        value = _extract_dropdown_value(saved_timbre_component)
        if value:
            return value

    for component in components.values():
        if isinstance(component, dict) and component.get("type") == "dropdown":
            value = _extract_dropdown_value(component)
            if value:
                return value
    return ""


def _extract_dropdown_value(component: object) -> str:
    if not isinstance(component, dict):
        return ""
    props = component.get("props")
    if not isinstance(props, dict):
        return ""

    value = props.get("value")
    if isinstance(value, str) and value:
        return value

    choices = props.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, str) and choice:
                return choice
            if isinstance(choice, list):
                for item in choice:
                    if isinstance(item, str) and item:
                        return item
    return ""


def _extract_uploaded_path(payload: object) -> str | None:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("path"), str):
            return str(payload["path"])
        if "files" in payload:
            return _extract_uploaded_path(payload["files"])
        if "data" in payload:
            return _extract_uploaded_path(payload["data"])
        return None
    if isinstance(payload, list):
        for item in payload:
            found = _extract_uploaded_path(item)
            if found:
                return found
    return None


def _extract_last_number(payload: object) -> int | float | None:
    found: int | float | None = None
    if isinstance(payload, bool):
        return None
    if isinstance(payload, (int, float)):
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            nested = _extract_last_number(value)
            if nested is not None:
                found = nested
    elif isinstance(payload, list):
        for item in payload:
            nested = _extract_last_number(item)
            if nested is not None:
                found = nested
    return found
