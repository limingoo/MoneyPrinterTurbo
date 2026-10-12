"""Runtime TTS extension: cloned voices (``svclone:<id>``) → SiliconFlow TTS.

Cloned voices are persisted in ``config.toml [app] cloned_voices`` (see
``app/controllers/v1/voice_clone.py``). At synthesis time the pipeline calls
``app.services.voice.tts()`` with the stored ``voice_name``; upstream dispatch
only understands ``siliconflow:<model>:<voice>`` style names, so this module
wraps ``voice._single_tts`` and resolves ``svclone:<id>`` entries back to
their stored ``speech:...`` URI before calling the stock ``siliconflow_tts``.

Everything lives in this new module; app/services/voice.py only runs
``install()`` at the bottom of the file (a few appended lines).
"""

import sys

from app.config import config as app_config

_PROVIDER_PREFIX = "svclone:"


def _resolve_entry(voice_name: str) -> dict | None:
    voice_id = voice_name.removeprefix(_PROVIDER_PREFIX)
    for item in app_config.app.get("cloned_voices") or []:
        if isinstance(item, dict) and item.get("id") == voice_id:
            return item
    return None


def _clone_tts(mod, text, voice_name, voice_rate, voice_file, voice_volume):
    from loguru import logger

    entry = _resolve_entry(voice_name)
    if not entry:
        logger.error(
            f"cloned voice not found in registry: {voice_name}; "
            "re-clone the voice in the settings page"
        )
        return None

    uri = str(entry.get("uri") or "").strip()
    model = str(entry.get("tts_model") or "FunAudioLLM/CosyVoice2-0.5B")
    if not uri:
        logger.error(f"cloned voice has empty uri: {voice_name}")
        return None

    # 终端是 GBK 编码时 emoji/特殊字符会写日志失败，这里做安全降级。
    label = str(entry.get("label") or "")
    safe_label = label.encode("gbk", errors="ignore").decode("gbk") or "unnamed"
    logger.info(f"svclone tts: voice={safe_label!r}, model={model}")
    try:
        return mod.siliconflow_tts(
            text, model, uri, voice_rate, voice_file, voice_volume
        )
    except Exception as exc:  # noqa: BLE001 - 与上游 TTS 行为一致：失败返回 None
        logger.error(f"svclone tts failed: {type(exc).__name__}: {exc}")
        return None


def install(fresh_module=None) -> bool:
    """包装 voice._single_tts，为 svclone: 前缀提供分发；其余完全走原实现。

    _single_tts 的签名（全部按位置或关键字传递，两者都要兼容）：
        _single_tts(text, voice_name, voice_rate, voice_file,
                    voice_volume=1.0, voxcpm_reference_audio=None,
                    voxcpm_prompt_audio=None, voxcpm_prompt_text="")
    """

    mod = fresh_module or sys.modules.get("app.services.voice")
    if mod is None or not hasattr(mod, "_single_tts"):
        return False
    if getattr(mod, "_voice_extensions_installed", False):
        return True

    original = mod._single_tts

    def patched_single_tts(
        text: str,
        voice_name: str,
        voice_rate: float,
        voice_file: str,
        voice_volume: float = 1.0,
        voxcpm_reference_audio=None,
        voxcpm_prompt_audio=None,
        voxcpm_prompt_text: str = "",
    ):
        if str(voice_name or "").startswith(_PROVIDER_PREFIX):
            return _clone_tts(mod, text, voice_name, voice_rate,
                              voice_file, voice_volume)
        return original(
            text=text,
            voice_name=voice_name,
            voice_rate=voice_rate,
            voice_file=voice_file,
            voice_volume=voice_volume,
            voxcpm_reference_audio=voxcpm_reference_audio,
            voxcpm_prompt_audio=voxcpm_prompt_audio,
            voxcpm_prompt_text=voxcpm_prompt_text,
        )

    mod._voice_extensions_installed = True
    mod._single_tts = patched_single_tts
    return True
