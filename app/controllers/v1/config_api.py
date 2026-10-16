"""Read and update runtime configuration over HTTP (for the web frontend).

The Streamlit WebUI mutates ``app.config.config`` section dicts in-process;
HTTP clients had no equivalent channel. This module exposes a masked read
endpoint and a whitelisted write endpoint that reuse the exact same deferred
save machinery (``update_config_nonblocking`` / ``delete_config_nonblocking``),
so values take effect immediately and are persisted to ``config.toml`` with the
same atomic, comment-preserving writer.

Secrets are never returned in plaintext: the read endpoint only reports whether
a value exists plus a masked preview. Writes follow "absent = unchanged",
empty string = remove key (fall back to defaults), any other value = set.

Everything lives in this new module; the only upstream touch is a two-line
router registration in ``app/router.py``.
"""

from typing import Literal

from fastapi import Depends, Request
from pydantic import BaseModel
from loguru import logger

from app.config import config as app_config
from app.config.config import (
    delete_config_nonblocking,
    try_save_config,
    update_config_nonblocking,
)
from app.controllers import base
from app.controllers.v1.base import new_router
from app.models.exception import HttpException
from app.models.llm_provider import LLM_PROVIDERS
# 确保运行时扩展的 provider（opencode_go）在任何加载顺序下都已注册。
from app.services import llm_extensions  # noqa: F401
from app.services import llm as llm_service
from app.utils import utils

router = new_router(dependencies=[Depends(base.verify_token)])

# 允许通过 HTTP 修改的图库检索密钥（config.toml [app] 内的扁平键）。
_STOCK_MATERIAL_KEYS = (
    "pexels_api_keys",
    "pixabay_api_keys",
    "coverr_api_keys",
)

_LLM_FIELD_SUFFIXES = ("api_key", "base_url", "model_name")

# AI 视频（文生视频）供应商注册表。每个字段映射到 [app] 里的扁平键并声明类型，
# GET 据此生成掩码视图，PUT 据此做类型转换。类型取值：
#   secret      -> 单个密钥，掩码返回
#   secret_list -> 逗号分隔的密钥列表，掩码返回
#   int         -> 整数（时长等）
#   str         -> 普通字符串
# 供应商顺序即前端展示顺序，与 WebUI 的 VIDEO_SOURCE_GROUPS 业务顺序一致：
# 秘塔 MiniMax → OFox → 火山 Seedance → WaveSpeed → MuAPI。
_AI_VIDEO_PROVIDERS: dict[str, dict] = {
    "metaso_minimax": {
        "label": "秘塔 MiniMax",
        "fields": [
            ("api_key", "metaso_minimax_api_key", "secret"),
            ("resolution", "metaso_minimax_resolution", "str"),
            ("base_url", "metaso_minimax_base_url", "str"),
        ],
        "defaults": {
            "base_url": "https://metaso.cn/api/minimax",
            # 服务端仅接受 768P / 2K；此处展示默认值，实际以服务端校验为准。
            "resolution": "2K",
        },
    },
    "ofox": {
        "label": "OFox AI",
        "fields": [
            ("api_key", "ofox_api_key", "secret"),
            ("base_url", "ofox_base_url", "str"),
            ("model", "ofox_text_to_video_model", "str"),
            ("resolution", "ofox_resolution", "str"),
            ("min_duration", "ofox_min_duration", "int"),
            ("max_duration", "ofox_max_duration", "int"),
            ("provider", "ofox_provider", "str"),
        ],
        "defaults": {
            "base_url": "https://api.ofox.ai/v1",
            "model": "bytedance/seedance-2.0-fast",
            "resolution": "720p",
            "min_duration": 4,
            "max_duration": 15,
            "provider": "byteplus",
        },
    },
    "volcengine_seedance": {
        "label": "火山引擎 Seedance",
        "fields": [
            ("api_key", "volcengine_seedance_api_key", "secret"),
            ("base_url", "volcengine_seedance_base_url", "str"),
            ("model", "volcengine_seedance_model", "str"),
            ("resolution", "volcengine_seedance_resolution", "str"),
            ("min_duration", "volcengine_seedance_min_duration", "int"),
            ("max_duration", "volcengine_seedance_max_duration", "int"),
        ],
        "defaults": {
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "doubao-seedance-1-0-pro-250528",
            "resolution": "1080p",
            "min_duration": 2,
            "max_duration": 12,
        },
    },
    "wavespeed": {
        "label": "WaveSpeed AI",
        "fields": [
            ("api_keys", "wavespeed_api_keys", "secret_list"),
            ("model", "wavespeed_text_to_video_model", "str"),
            ("min_duration", "wavespeed_min_duration", "int"),
            ("max_duration", "wavespeed_max_duration", "int"),
        ],
        "defaults": {
            "model": "bytedance/seedance-2.0-fast/text-to-video",
            "min_duration": 4,
            "max_duration": 15,
        },
    },
    "muapi": {
        "label": "MuAPI",
        "fields": [
            ("api_key", "muapi_api_key", "secret"),
            # video_endpoint 即 MuAPI 的模型端点（如 seedance-lite-t2v）。
            ("video_endpoint", "muapi_video_endpoint", "str"),
            ("resolution", "muapi_resolution", "str"),
            ("min_duration", "muapi_min_duration", "int"),
            ("max_duration", "muapi_max_duration", "int"),
            ("base_url", "muapi_base_url", "str"),
        ],
        "defaults": {
            "video_endpoint": "seedance-lite-t2v",
            "resolution": "480p",
            "min_duration": 3,
            "max_duration": 12,
            "base_url": "https://api.muapi.ai/api/v1",
        },
    },
}

# 可用于默认素材源（video_source）的全部取值，与 task.py 的受支持来源一致。
_KNOWN_VIDEO_SOURCES = (
    "pexels",
    "pixabay",
    "coverr",
    "wavespeed",
    "volcengine_seedance",
    "ofox",
    "metaso_minimax",
    "muapi",
    "loomloom",
    "openai_image",
    "local",
)

_MASK_VISIBLE_CHARS = 4

# OpenAI 兼容网关支持 GET /models 自动发现模型列表；其他/provider 类别
# 一律走静态清单回退。设置页的模型下拉会先尝试动态列表。
_MODEL_LIST_TIMEOUT_SECONDS = 8

# 无官方 /models 端点或拉取失败时使用的静态推荐清单。
_STATIC_MODEL_CATALOGS: dict[str, tuple[str, ...]] = {
    "opencode_go": (
        "glm-5.3-flash",
        "glm-5.3",
        "glm-5.2",
        "kimi-k3",
        "kimi-k2.7-code",
        "kimi-k2.6",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "minimax-m3",
        "qwen3.8-max",
        "qwen3.8-flash",
        "grok-4.7",
        "gpt-6-luna",
        "claude-haiku-5-5",
    ),
}


def _mask_secret(value) -> dict:
    """密钥只回传存在性与掩码，绝不返回明文。"""

    text = str(value or "").strip()
    if not text:
        return {"has_value": False, "masked": ""}
    if len(text) <= _MASK_VISIBLE_CHARS:
        return {"has_value": True, "masked": "••••"}
    return {"has_value": True, "masked": f"••••{text[-_MASK_VISIBLE_CHARS:]}"}


def _ai_video_view(app_section) -> dict:
    """生成 AI 视频供应商的掩码视图，并标注每个供应商是否已配置可用。

    `enabled` 只看密钥是否已配置（含列表型），足以驱动前端"只列已配置来源"。
    """

    view: dict[str, dict] = {}
    for provider_id, spec in _AI_VIDEO_PROVIDERS.items():
        fields: dict[str, dict] = {}
        enabled = False
        for name, config_key, kind in spec["fields"]:
            raw = app_section.get(config_key)
            default = spec["defaults"].get(name, "")
            if kind == "secret":
                masked = _mask_secret(raw)
                fields[name] = {"kind": kind, **masked, "default": default}
                enabled = enabled or masked["has_value"]
            elif kind == "secret_list":
                items = raw if isinstance(raw, list) else []
                masked = _mask_secret(items[0] if items else "")
                fields[name] = {
                    "kind": kind,
                    "has_value": bool(items),
                    "masked": masked["masked"],
                    "count": len(items),
                    "default": default,
                }
                enabled = enabled or bool(items)
            else:
                value = raw if raw not in (None, "") else default
                fields[name] = {"kind": kind, "value": value, "default": default}
        view[provider_id] = {"label": spec["label"], "enabled": enabled, "fields": fields}
    return view


def _provider_meta():
    providers = []
    for spec in LLM_PROVIDERS.values():
        providers.append(
            {
                "id": spec.provider_id,
                "label": spec.default_label,
                "default_model": spec.default_model,
                "default_base_url": spec.default_base_url,
                "requires_api_key": spec.requires_api_key,
                "requires_base_url": spec.requires_base_url,
                "requires_model_name": spec.requires_model_name,
                "show_api_key": spec.show_api_key,
                "show_base_url": spec.show_base_url,
            }
        )
    return providers


@router.get("/config", summary="Read masked runtime configuration")
def get_runtime_config(request: Request):
    request_id = base.get_task_id(request)
    app_section = app_config.app

    provider_id = str(app_section.get("llm_provider", "") or "")
    if provider_id not in LLM_PROVIDERS:
        from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID

        provider_id = DEFAULT_LLM_PROVIDER_ID
    spec = LLM_PROVIDERS[provider_id]

    llm_values = {
        "api_key": _mask_secret(app_section.get(f"{provider_id}_api_key")),
        "base_url": {"value": str(app_section.get(f"{provider_id}_base_url", "") or "")},
        "model_name": {"value": str(app_section.get(f"{provider_id}_model_name", "") or "")},
    }

    materials = {}
    for key in _STOCK_MATERIAL_KEYS:
        raw = app_section.get(key) or []
        materials[key] = {
            "count": len(raw) if isinstance(raw, list) else 0,
            "masked": _mask_secret(raw[0] if isinstance(raw, list) and raw else "")["masked"],
        }
    materials["video_source"] = str(app_section.get("video_source", "pexels") or "pexels")

    tts_section = {
        "siliconflow_api_key": _mask_secret(app_config.siliconflow.get("api_key")),
    }
    cloned_voices = [item for item in (app_section.get("cloned_voices") or [])
                     if isinstance(item, dict)]

    return utils.get_response(
        200,
        {
            "llm": {
                "provider": provider_id,
                "providers": _provider_meta(),
                "values": llm_values,
                "current_spec": {
                    "default_model": spec.default_model,
                    "default_base_url": spec.default_base_url,
                    "requires_api_key": spec.requires_api_key,
                    "requires_base_url": spec.requires_base_url,
                    "requires_model_name": spec.requires_model_name,
                    "show_api_key": spec.show_api_key,
                    "show_base_url": spec.show_base_url,
                },
            },
            "materials": materials,
            "ai_video": _ai_video_view(app_section),
            "tts": tts_section,
            "cloned_voices": cloned_voices,
        },
    )


class LLMConfigUpdate(BaseModel):
    # None = 不修改；空字符串 = 清除（回退默认值）；非空 = 设置。
    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model_name: str | None = None


class MaterialsUpdate(BaseModel):
    # None = 不修改；字符串按逗号/换行拆分为列表（空串 = 清空）。
    pexels_api_keys: str | None = None
    pixabay_api_keys: str | None = None
    coverr_api_keys: str | None = None
    # 默认素材源（写入 [app] video_source）。
    video_source: str | None = None


class AIVideoProviderUpdate(BaseModel):
    # 只提交要改的字段；None = 不修改，空串 = 清除回退默认值。
    # 整数字段留空时前端应省略或传 null，避免空串触发校验失败。
    api_key: str | None = None
    api_keys: str | None = None
    base_url: str | None = None
    model: str | None = None
    video_endpoint: str | None = None
    resolution: str | None = None
    min_duration: int | None = None
    max_duration: int | None = None
    provider: str | None = None


class TTSConfigUpdate(BaseModel):
    # 声音克隆相关的 TTS 密钥；None = 不修改，空字符串 = 清除。
    siliconflow_api_key: str | None = None


class ConfigUpdateRequest(BaseModel):
    llm: LLMConfigUpdate | None = None
    materials: MaterialsUpdate | None = None
    ai_video: dict[str, AIVideoProviderUpdate] | None = None
    tts: TTSConfigUpdate | None = None


_models_cache: dict[str, tuple[float, list[str]]] = {}
_MODELS_CACHE_TTL_SECONDS = 600.0


def _fetch_provider_models(provider_id: str) -> list[str] | None:
    """从 OpenAI 兼容网关拉取模型列表；未配置 Key 或请求失败时返回 None。

    结果缓存 10 分钟：模型目录变化低频，避免每次打开设置页都访问外网。
    """

    import time

    cached = _models_cache.get(provider_id)
    if cached and time.time() - cached[0] < _MODELS_CACHE_TTL_SECONDS:
        return cached[1]

    spec = LLM_PROVIDERS.get(provider_id)
    if spec is None or spec.adapter != "openai_compatible":
        return None

    api_key = str(app_config.app.get(f"{provider_id}_api_key", "") or "").strip()
    base_url = spec.resolve_base_url(
        str(app_config.app.get(f"{provider_id}_base_url", "") or "")
    )
    if not api_key:
        return None

    # 兼容网关同时支持下的 /models；非标准 base_url（如带 /chat 后缀）直接放弃。
    try:
        import requests

        headers = {"Authorization": f"Bearer {api_key}"}
        # OpenCode Go 要求所有请求携带会话标识，/models 也不例外。
        if provider_id == "opencode_go":
            from app.services.llm_extensions import request_headers

            headers.update(request_headers())
        response = requests.get(
            f"{base_url.rstrip('/')}/models",
            headers=headers,
            timeout=_MODEL_LIST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return None
        models = sorted(
            {
                str(item.get("id")).strip()
                for item in items
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
        )
    except Exception:  # noqa: BLE001 - 网络失败不影响设置页其余功能
        return None

    if not models:
        return None
    _models_cache[provider_id] = (time.time(), models)
    return models


@router.get("/config/models", summary="List models for the current LLM provider")
def list_provider_models(request: Request, provider: str | None = None):
    request_id = base.get_task_id(request)
    provider_id = (
        provider
        if provider and provider in LLM_PROVIDERS
        else str(app_config.app.get("llm_provider", "") or "")
    )
    if provider_id not in LLM_PROVIDERS:
        from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID

        provider_id = DEFAULT_LLM_PROVIDER_ID

    models = _fetch_provider_models(provider_id)
    source = "api"
    if models is None:
        catalog = _STATIC_MODEL_CATALOGS.get(provider_id)
        if catalog:
            models = list(catalog)
            source = "static"
        elif LLM_PROVIDERS[provider_id].default_model:
            models = [LLM_PROVIDERS[provider_id].default_model]
            source = "default"

    return utils.get_response(
        200,
        {
            "provider": provider_id,
            "models": models or [],
            "source": source if models else "unavailable",
        },
    )


def _split_keys(raw: str | None) -> list[str]:
    if raw is None:
        return []
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


@router.put("/config", summary="Update whitelisted runtime configuration")
def update_runtime_config(request: Request, body: ConfigUpdateRequest):
    request_id = base.get_task_id(request)
    app_section = app_config.app
    updated: list[str] = []

    if body.llm is not None:
        provider = body.llm.provider
        if provider is not None:
            if provider not in LLM_PROVIDERS:
                raise HttpException(
                    task_id=request_id,
                    status_code=400,
                    message=f"{request_id}: unknown llm provider '{provider}'",
                )
            update_config_nonblocking(app_section, "llm_provider", provider)
            updated.append("llm_provider")

        # provider 可能与 api_key 同批修改；字段解析始终跟随最终 provider。
        target_provider = provider or str(app_section.get("llm_provider", "") or "")
        if target_provider not in LLM_PROVIDERS:
            from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID

            target_provider = DEFAULT_LLM_PROVIDER_ID

        for suffix in _LLM_FIELD_SUFFIXES:
            value = getattr(body.llm, suffix)
            if value is None:
                continue
            key = f"{target_provider}_{suffix}"
            if value == "":
                delete_config_nonblocking(app_section, key)
            else:
                update_config_nonblocking(app_section, key, value.strip())
            updated.append(key)

    if body.materials is not None:
        for key in _STOCK_MATERIAL_KEYS:
            value = getattr(body.materials, key)
            if value is None:
                continue
            update_config_nonblocking(app_section, key, _split_keys(value))
            updated.append(key)

        # 默认素材源：空串 = 清除（回退代码默认），否则必须是已知来源。
        video_source = body.materials.video_source
        if video_source is not None:
            if video_source == "":
                delete_config_nonblocking(app_section, "video_source")
            elif video_source not in _KNOWN_VIDEO_SOURCES:
                raise HttpException(
                    task_id=request_id,
                    status_code=400,
                    message=f"{request_id}: unknown video source '{video_source}'",
                )
            else:
                update_config_nonblocking(app_section, "video_source", video_source)
            updated.append("video_source")

    if body.ai_video is not None:
        for provider_id, payload in body.ai_video.items():
            spec = _AI_VIDEO_PROVIDERS.get(provider_id)
            if spec is None:
                raise HttpException(
                    task_id=request_id,
                    status_code=400,
                    message=f"{request_id}: unknown ai video provider '{provider_id}'",
                )
            for name, config_key, kind in spec["fields"]:
                value = getattr(payload, name, None)
                if value is None:
                    continue
                if kind == "secret_list":
                    update_config_nonblocking(app_section, config_key, _split_keys(value))
                elif kind == "int":
                    update_config_nonblocking(app_section, config_key, int(value))
                elif value == "":
                    delete_config_nonblocking(app_section, config_key)
                else:
                    update_config_nonblocking(app_section, config_key, value.strip())
                updated.append(config_key)

    if body.tts is not None:
        value = body.tts.siliconflow_api_key
        if value is not None:
            if value == "":
                delete_config_nonblocking(app_config.siliconflow, "api_key")
            else:
                update_config_nonblocking(app_config.siliconflow, "api_key", value.strip())
            updated.append("siliconflow_api_key")

    logger.info(f"runtime config updated via API: keys={updated}")
    # 与 WebUI 相同的两步：先更新内存，再尝试原子写回 config.toml。
    # 锁繁忙时 try_save_config 会转入延迟保存，最终一定落盘。
    saved = try_save_config()
    return utils.get_response(200, {"updated": updated, "saved": saved})


@router.post(
    "/config/test_llm",
    summary="Send a tiny LLM request to verify the current configuration",
)
def test_llm_config(request: Request):
    request_id = base.get_task_id(request)
    try:
        terms = llm_service.generate_terms(
            video_subject="sunset over the city",
            video_script="",
            amount=1,
            match_script_order=False,
        )
    except Exception as exc:  # noqa: BLE001 - 任何失败都作为可展示的测试结果返回
        return utils.get_response(200, {"ok": False, "error": str(exc)})

    if not terms:
        return utils.get_response(
            200, {"ok": False, "error": "provider returned an empty response"}
        )
    return utils.get_response(200, {"ok": True, "sample_terms": terms[:3]})
