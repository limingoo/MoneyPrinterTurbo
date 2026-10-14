"""Voice cloning via SiliconFlow (voice-clone uploads + persistent registry).

克隆采用 SiliconFlow 的声音复刻 API（`POST /v1/uploads/audio/voice`）：
上传一段 10-30 秒的干净人声样本，返回一个 ``speech:<name>:<uid>:<sid>`` URI，
后续用 CosyVoice2 模型直接以该 URI 合成。

克隆成功的声音以 ``svclone:<id>`` 形式的 voice_name 持久化在
``config.toml [app] cloned_voices``（由 app/services/voice_extensions.py 在
TTS 分发时解析回 URI），从而出现在前端"我的克隆声音"分组里。

该模块只新增端点，上游文件仅 router.py 注册两行。
"""

import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import Depends, File, Form, Request, UploadFile
from loguru import logger
from pydantic import BaseModel

from app.config import config as app_config
from app.config.config import (
    delete_config_nonblocking,
    try_save_config,
    update_config_nonblocking,
)
from app.controllers import base
from app.controllers.v1.base import new_router
from app.models.exception import HttpException
from app.utils import utils

router = new_router(dependencies=[Depends(base.verify_token)])

_CLONE_API_URL = "https://api.siliconflow.cn/v1/uploads/audio/voice"
_SILICONFLOW_TTS_MODEL = "FunAudioLLM/CosyVoice2-0.5B"
_MAX_AUDIO_BYTES = 8 * 1024 * 1024  # SiliconFlow 音色样本上限
_ALLOWED_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac")
_UPLOAD_TIMEOUT_SECONDS = 120
_TRANSCODE_TIMEOUT_SECONDS = 120

# SiliconFlow 上游只认 wav/mp3/pcm/opus。前端文件选择器是 audio/*，手机
# （尤其是 iPhone）录出来的是 .m4a，本地白名单放行后会被上游以
# "Only wav/mp3/pcm/opus are supported" 400 拒绝。与其让用户手动转格式，
# 不如在上传前用 ffmpeg 转成上游必定接受的 mp3。
_UPSTREAM_NATIVE_EXTENSIONS = (".mp3", ".wav")
_TRANSCODE_TARGET_SUFFIX = ".mp3"
# 16k 单声道是音色复刻的常规输入规格，体积也远小于原始 m4a。
_TRANSCODE_AUDIO_ARGS = (
    "-vn",
    "-ac", "1",
    "-ar", "16000",
    "-c:a", "libmp3lame",
    "-b:a", "64k",
)


class ClonedVoiceEntry(BaseModel):
    id: str
    label: str
    voice_name: str
    uri: str
    provider: str = "siliconflow"
    created_at: str = ""


def _cloned_voices() -> list[dict]:
    raw = app_config.app.get("cloned_voices") or []
    return [item for item in raw if isinstance(item, dict) and item.get("id")]


def _save_cloned_voices(items: list[dict]) -> None:
    update_config_nonblocking(app_config.app, "cloned_voices", items)
    try_save_config()


def _sanitize_custom_name(name: str, request_id: str) -> str:
    """SiliconFlow customName 对字符集较敏感；规范化为安全的英文标识。"""

    cleaned = "".join(c for c in (name or "").strip() if c.isalnum() or c in "-_")
    # 中文等非 ASCII 名称无法直接作为 customName 时退化成随机名，展示名仍用原文。
    cleaned = "".join(c if c.isascii() else "" for c in cleaned).strip("-_")
    # 连续符号压缩，避免空标识或超长标识。
    while "--" in cleaned or "__" in cleaned:
        cleaned = cleaned.replace("--", "-").replace("__", "_")
    if not cleaned:
        cleaned = f"mpt-{uuid.uuid4().hex[:8]}"
    return cleaned[:40]


def _get_siliconflow_key(request_id: str) -> str:
    api_key = str(app_config.siliconflow.get("api_key", "") or "").strip()
    if not api_key:
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=(
                f"{request_id}: 请先在设置页填写 SiliconFlow API Key"
                "（siliconflow.cn 注册后免费获得额度）"
            ),
        )
    return api_key


def _transcode_to_accepted_format(payload: bytes, suffix: str, request_id: str):
    """把上游不接受的音频转成 mp3，返回 ``(文件名, 字节)``。

    上传流在内存里，ffmpeg 需要真实文件才能读取，因此借临时文件中转；
    成功和失败都在 finally 里删除，不会留下残留。
    """

    descriptor, source_path = tempfile.mkstemp(
        prefix="voice-clone-src-", suffix=suffix or ".bin"
    )
    descriptor2, target_path = tempfile.mkstemp(
        prefix="voice-clone-out-", suffix=_TRANSCODE_TARGET_SUFFIX
    )
    os.close(descriptor)
    os.close(descriptor2)
    try:
        with open(source_path, "wb") as handle:
            handle.write(payload)

        command = [
            utils.get_ffmpeg_binary(),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            source_path,
            *_TRANSCODE_AUDIO_ARGS,
            target_path,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=_TRANSCODE_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()[:200]
            logger.warning(
                f"voice clone transcode failed: request_id={request_id}, "
                f"suffix={suffix!r}, returncode={completed.returncode}"
            )
            raise HttpException(
                task_id=request_id,
                status_code=400,
                message=(
                    f"{request_id}: 无法转换 {suffix or '该'} 音频为上游支持的格式"
                    f"（ffmpeg 退出码 {completed.returncode}）{detail}"
                    "；请改用 wav 或 mp3 文件"
                ),
            )

        with open(target_path, "rb") as handle:
            converted = handle.read()
        if not converted:
            raise HttpException(
                task_id=request_id,
                status_code=400,
                message=f"{request_id}: 音频转换为 mp3 后为空，请更换文件",
            )
        if len(converted) > _MAX_AUDIO_BYTES:
            raise HttpException(
                task_id=request_id,
                status_code=400,
                message=f"{request_id}: 转换后的音频超过 8MB 上限，请裁剪到 10-30 秒",
            )

        logger.info(
            f"voice clone transcoded {suffix or 'unknown'} -> mp3: "
            f"request_id={request_id}, {len(payload)} -> {len(converted)} bytes"
        )
        stem = Path(target_path).stem
        return f"{stem}{_TRANSCODE_TARGET_SUFFIX}", converted
    except subprocess.TimeoutExpired as exc:
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=f"{request_id}: 音频转换超时，请裁剪到 10-30 秒后重试",
        ) from exc
    finally:
        for path in (source_path, target_path):
            try:
                os.unlink(path)
            except OSError:
                pass


@router.post(
    "/voice_clone",
    summary="Clone a voice with SiliconFlow and store it in the voice list",
    description=(
        "Multipart: `name` 展示名，`text` 为音频对应的文字（强烈建议提供，"
        "显著提升克隆自然度），`file` 为 10-30 秒干净人声样本。"
        "成功后自动加入 config.toml [app].cloned_voices 并出现在音色列表。"
    ),
)
async def clone_voice(
    request: Request,
    name: str = Form(...),
    text: str = Form(""),
    file: UploadFile = File(...),
):
    request_id = base.get_task_id(request)
    api_key = _get_siliconflow_key(request_id)

    filename = (file.filename or "").replace("\\", "/").split("/")[-1].strip()
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=(
                f"{request_id}: 音频格式需为 mp3/wav/m4a/aac，"
                f"收到 {suffix or '未知'}"
            ),
        )

    payload = await file.read()
    if not payload:
        raise HttpException(
            task_id=request_id, status_code=400, message=f"{request_id}: 音频为空"
        )
    if len(payload) > _MAX_AUDIO_BYTES:
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=f"{request_id}: 音频超过 8MB 上限，请裁剪到 10-30 秒",
        )

    transcript = (text or "").strip()
    if not transcript:
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=f"{request_id}: 请提供音频对应的文字（内容必须与录音逐字一致）",
        )

    # 本地白名单与上游支持格式不一致的格式（.m4a/.aac）在这里先转成 mp3，
    # 避免把必然被上游 400 拒绝的字节发出去。
    if suffix not in _UPSTREAM_NATIVE_EXTENSIONS:
        filename, payload = _transcode_to_accepted_format(payload, suffix, request_id)

    custom_name = _sanitize_custom_name(name, request_id)
    display_name = (name or "").strip() or custom_name

    # SiliconFlow 返回 {"uri": "speech:<name>:<uid>:<sid>"}；
    # 非 200 时的 message 会带出配额不足、音质过低等业务原因。
    try:
        response = requests.post(
            _CLONE_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": _SILICONFLOW_TTS_MODEL,
                "customName": custom_name,
                "text": transcript,
            },
            # 官方文档的文件字段名是 "file"（"audio" 是 base64 方式的字段名）。
            files={"file": (filename or f"voice{suffix}", payload)},
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise HttpException(
            task_id=request_id,
            status_code=502,
            message=f"{request_id}: 无法连接 SiliconFlow（{type(exc).__name__}）",
        )

    if response.status_code != 200:
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("message") or body.get("data") or "")[:200]
        except Exception:  # noqa: BLE001 - 非 JSON 响应也给出可读提示
            detail = response.text[:200]
        logger.warning(
            f"voice clone rejected: request_id={request_id}, "
            f"status={response.status_code}, detail={detail!r}"
        )
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=f"{request_id}: 克隆失败（HTTP {response.status_code}）{detail}",
        )

    try:
        body = response.json()
        uri = (body.get("uri") or (body.get("data") or {}).get("uri") or "").strip()
    except Exception:  # noqa: BLE001
        uri = ""
    if not uri:
        raise HttpException(
            task_id=request_id,
            status_code=500,
            message=f"{request_id}: SiliconFlow 未返回音色 URI",
        )

    # 同名 customName 重复克隆时会覆盖云端同 ID；本地列表按 id 去重。
    entry_id = uuid.uuid4().hex[:12]
    entry = {
        "id": entry_id,
        "label": f"🎙 {display_name}",
        "voice_name": f"svclone:{entry_id}",
        "uri": uri,
        "provider": "siliconflow",
        "tts_model": _SILICONFLOW_TTS_MODEL,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    items = [item for item in _cloned_voices() if item.get("id") != entry_id]
    items.append(entry)
    _save_cloned_voices(items)
    logger.info(f"voice cloned and stored: id={entry_id}, name={display_name!r}")

    return utils.get_response(200, entry)


@router.get("/voice_clones", summary="List cloned voices")
def list_cloned_voices(request: Request):
    return utils.get_response(200, {"voices": _cloned_voices()})


class VoiceCloneDeleteRequest(BaseModel):
    id: str


@router.post("/voice_clones/delete", summary="Remove a cloned voice from the list")
def remove_cloned_voice(request: Request, body: VoiceCloneDeleteRequest):
    request_id = base.get_task_id(request)
    remaining = [item for item in _cloned_voices() if item.get("id") != body.id]
    if len(remaining) == len(_cloned_voices()):
        raise HttpException(
            task_id=request_id, status_code=404, message=f"{request_id}: 未找到该克隆声音"
        )
    # 删除云端音色没有官方 API（SiliconFlow 端音色永久存在），仅移除本地引用。
    if not remaining:
        delete_config_nonblocking(app_config.app, "cloned_voices")
    else:
        _save_cloned_voices(remaining)
    return utils.get_response(200, {"removed": body.id})
