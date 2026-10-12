"""Register "OpenCode Go" as an LLM provider at runtime.

OpenCode Go (https://opencode.ai/go) is the official low-cost subscription
gateway from the OpenCode team: one API key, models such as GLM-5.3, Kimi K3,
DeepSeek V4, MiniMax M3 and Qwen3.8.

Go is not a plain OpenAI-compatible gateway: per the official docs it requires

1. a stable ``x-opencode-session`` header (used for routing / prompt caching),
2. an identifying ``User-Agent`` (single-tenant monitoring rejects generic SDK UAs),
3. model-dependent endpoints — catalogs are split across
   ``/v1/chat/completions`` (OpenAI style), ``/v1/messages`` (Anthropic style)
   and ``/v1/responses`` (OpenAI Responses style).

Upstream ``app/services/llm.py`` dispatches providers through an adapter chain
and vanilla ``OpenAI(...)`` clients, none of which attach custom headers. To
stay zero-touch on upstream files, this module:

* registers the provider in place (``LLM_PROVIDERS`` dict mutation), and
* wraps ``llm._generate_response`` at install() time — requests for any other
  provider are delegated to the original function untouched, while
  ``opencode_go`` requests are served here with the required headers and the
  correct endpoint per model.

Catalog per https://opencode.ai/docs/go/ (endpoints table), 2026-10.
"""

import sys
import uuid

from app.models import llm_provider as _lp
from app.models.llm_provider import LLMProviderSpec

_PROVIDER_ID = "opencode_go"

# ---- Endpoint routing per official docs ------------------------------------
# 直接支持 OpenAI SDK chat.completions 的模型。
_CHAT_COMPLETIONS_MODELS = frozenset(
    {
        "glm-5.3-flash",
        "glm-5.3",
        "glm-5.2",
        "kimi-k3",
        "kimi-k2.7-code",
        "kimi-k2.6",
        "longcat-2.0",
        "longcat-2.5-preview-free",
        "step-5-preview-free",
        "deepseek-v4.1-flash",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
        "mimo-v2.6-flash",
        "mimo-v2.6-pro",
        "mimo-v2.5",
        "mimo-v2.5-pro",
        "hy4-preview",
        "hy3",
        "space-bunny",
    }
)
# Anthropic 风格 /messages 端点的模型（MiniMax、Qwen、Claude Haiku）。
_ANTHROPIC_MODELS = frozenset(
    {
        "minimax-m3",
        "minimax-m2.7",
        "qwen3.8-max",
        "qwen3.8-flash",
        "qwen3.7-plus",
        "claude-haiku-5-5",
    }
)
# /responses（OpenAI Responses API）独占的模型；SDK 的 chat.completions
# 无法访问这些端点，命中时给出明确提示而不是神秘报错。
_RESPONSES_ONLY_MODELS = frozenset(
    {
        "grok-4.7",
        "grok-4.6",
        "gpt-6-luna",
        "gpt-5.6-luna",
        "muse-spark-1.3-contributor",
        "muse-spark-1.2-contributor",
    }
)

_STATIC_MODEL_CATALOG = tuple(
    sorted(_CHAT_COMPLETIONS_MODELS | _ANTHROPIC_MODELS)
)

_ANTHROPIC_VERSION = "2023-06-01"
_MAX_COMPLETION_TOKENS = 1600


def _stable_session_id() -> str:
    """进程级稳定会话 ID（官方要求：每个会话稳定，用于路由与缓存优化）。"""

    if not _stable_session_id.value:  # type: ignore[attr-defined]
        import os

        # 环境变量优先，便于聚合统计；否则实例化一个稳定 UUID。
        _stable_session_id.value = (
            os.getenv("MPT_OPENCODE_SESSION_ID") or str(uuid.uuid4())
        )
    return _stable_session_id.value


_stable_session_id.value = ""


def request_headers() -> dict[str, str]:
    """Go 网关要求的身份头；/models 拉取与 chat 请求共用。"""

    return {
        "x-opencode-session": _stable_session_id(),
        "User-Agent": "MoneyPrinterTurbo/1.3.8 (video-script-gateway)",
    }


# ---- Provider registration (in place) --------------------------------------
if _PROVIDER_ID not in _lp.LLM_PROVIDERS:
    _spec = LLMProviderSpec(
        _PROVIDER_ID,
        "OpenCode Go",
        # 走 llm.py 的通用 OpenAI 兼容分支；Go 特殊的会话头/端点路由
        # 由下方 _generate_response 拦截器补充。
        adapter="openai_compatible",
        api_key_url="https://opencode.ai/auth",
        default_model="glm-5.3-flash",
        default_base_url="https://opencode.ai/zen/go/v1",
        model_docs_url="https://opencode.ai/docs/go/",
        requires_api_key=True,
        requires_model_name=True,
        requires_base_url=False,
    )
    # 原地写入：LLM_PROVIDERS 是被多方共享的 dict，重新绑定不会传播。
    _lp.LLM_PROVIDERS[_PROVIDER_ID] = _spec
    _lp.LLM_PROVIDER_REGISTRY = (*_lp.LLM_PROVIDER_REGISTRY, _spec)


def static_model_catalog() -> tuple[str, ...]:
    return _STATIC_MODEL_CATALOG


def _resolve_runtime(app_config):
    if app_config is not None:
        return app_config
    from app.config import config

    return config.app


def _call_chat_completions(endpoint: str, api_key: str, model: str, prompt: str):
    """OpenAI 风格 /chat/completions（带 Go 必需的身份头）。"""

    from openai import OpenAI

    with OpenAI(api_key=api_key, base_url=endpoint, default_headers=request_headers()) as client:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        if not response or not response.choices:
            raise ValueError("empty response from opencode_go")
        content = response.choices[0].message.content or ""
        if not content.strip():
            raise ValueError("empty content from opencode_go")
        return content


def _call_anthropic_messages(endpoint_root: str, api_key: str, model: str, prompt: str):
    """Anthropic 风格 /messages（MiniMax、Qwen、Claude Haiku 5.5）。"""

    import requests

    url = endpoint_root.rstrip("/").removesuffix("/v1") + "/v1/messages"
    resp = requests.post(
        url,
        headers={
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
            **request_headers(),
        },
        json={
            "model": model,
            "max_tokens": _MAX_COMPLETION_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    parts = [
        block.get("text", "")
        for block in payload.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    content = "\n".join(part for part in parts if part).strip()
    if not content:
        raise ValueError("empty content from opencode_go")
    return content


# ---- _generate_response interceptor ----------------------------------------
def install(fresh_module=None) -> bool:
    """把 opencode_go 专用请求分支挂到 llm._generate_response 之前。

    只在 llm 模块加载完成（存在 _generate_response）且尚未安装时执行一次；
    其余 provider 全部委托原始实现，行为与上游完全一致。
    """

    mod = fresh_module or sys.modules.get("app.services.llm")
    if mod is None or not hasattr(mod, "_generate_response"):
        return False
    if getattr(mod, "_opencode_go_installed", False):
        return True

    original = mod._generate_response

    def patched_generate_response(prompt, app_config=None):
        runtime = _resolve_runtime(app_config)
        if str(runtime.get("llm_provider", "")).lower() != _PROVIDER_ID:
            return original(prompt, app_config=app_config)

        spec = _lp.LLM_PROVIDERS[_PROVIDER_ID]
        api_key = str(runtime.get(spec.config_key("api_key"), "") or "").strip()
        if not api_key:
            return "Error: opencode_go: api_key is not set"
        model_name = spec.resolve_model_name(
            runtime.get(spec.config_key("model_name"))
        )
        base_url = spec.resolve_base_url(
            runtime.get(spec.config_key("base_url"), "")
        ).rstrip("/")

        try:
            if model_name in _ANTHROPIC_MODELS:
                text = _call_anthropic_messages(base_url, api_key, model_name, prompt)
            elif model_name in _RESPONSES_ONLY_MODELS:
                raise ValueError(
                    f"{model_name} 使用 OpenAI Responses 端点，暂不支持；"
                    f"请选择 glm-5.3-flash / kimi-k3 / deepseek-v4-pro 等"
                    f" chat/completions 或 messages 模型"
                )
            else:
                text = _call_chat_completions(base_url, api_key, model_name, prompt)
            return mod._normalize_text_response(text, _PROVIDER_ID)
        except Exception as exc:  # noqa: BLE001 - 与上游一致：错误以字符串返回
            return f"Error: {mod._sanitize_error_message(exc)}"

    mod._opencode_go_installed = True
    mod._generate_response = patched_generate_response
    return True
