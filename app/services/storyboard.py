"""Storyboard splitting shared by the HTTP endpoint and the render pipeline.

把整段文案交给当前 LLM 提供商，拆成若干分镜（句子原文 + 为该句挑选画面的
简短中文关键词）。两个调用方共用这里的实现：

* `app/controllers/v1/storyboard.py`：前端创作页在提交后核对逐镜头缺口；
* `app/services/task.py`：本地素材源在渲染前按分镜**规划画面顺序与时长**，
  让旁白念到哪一句、画面就切到哪一条素材（`shot_alignment` 消费这里的输出）。

统一走 llm 服务的响应链（含 OpenCode Go 等运行时扩展的本会话头/端点路由），
输出做 JSON 容错解析；提示词与解析规则集中在这里，控制器只负责 HTTP 语义。
"""

import json
import re
import time

from loguru import logger

DEFAULT_MAX_SHOTS = 10
MAX_SHOTS_LIMIT = 12
MIN_SHOTS = 3
# OpenCode Go 网关常有 >120s 的瞬时抖动，重试一次吸收间隙性失败。
DEFAULT_ATTEMPTS = 2
DEFAULT_RETRY_DELAY_SECONDS = 15.0

_ERROR_PREFIX = "Error: "


class StoryboardError(RuntimeError):
    """分镜拆分失败（LLM 报错或返回无法解析）。"""


def build_prompt(subject: str, script: str, max_shots: int) -> str:
    return (
        "你是一名短视频分镜师。请把下面的视频文案拆分成若干分镜，用于按叙述顺序挑选画面素材。\n\n"
        "要求：\n"
        f"1. 分镜数量 {MIN_SHOTS}~{max_shots} 个，按文案叙述顺序排列；\n"
        "2. 每个分镜的 text 必须是文案中的**原句或原句合并**，不要改写、不要省略、合并后要能覆盖全部文案；\n"
        "3. 每个分镜的字段：\n"
        "   - term：画面中文关键词（2~6 个字，如：面部按摩、樱花特写），具体到可搜索画面，贴合主题；\n"
        "   - title：给拍摄者看的简短标题，格式「内容：对象」如「手法特写：面部清洁按摩」；\n"
        "   - brief：拍摄简报，40 字以内，说明画面内容、主体、景别/机位与注意事项\n"
        "     （如：美容师双手在顾客面部打圈清洁的手部特写，近景稳定，光线柔和）；\n"
        "4. 只输出 JSON 数组，不要任何其他文字，格式：\n"
        '[{"text": "原句", "term": "关键词", "title": "简短标题", "brief": "拍摄简报"}]\n\n'
        f"视频主题：{subject or '（从文案推断）'}\n"
        f"视频文案：\n{script}"
    )


def parse_shots(text: str) -> list[dict]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError("LLM 未返回 JSON 数组")
    payload = json.loads(match.group(0))
    shots: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        line = str(item.get("text") or "").strip()
        term = str(item.get("term") or "").strip()
        title = str(item.get("title") or "").strip()
        brief = str(item.get("brief") or "").strip()
        if line and term:
            shots.append(
                {
                    "text": line,
                    "term": term,
                    "title": title or term,
                    "brief": brief,
                }
            )
    return shots


def _is_error_response(response: str) -> bool:
    return not response or response.startswith(_ERROR_PREFIX)


def normalize_max_shots(max_shots: int | None) -> int:
    try:
        value = int(max_shots)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = DEFAULT_MAX_SHOTS
    return max(MIN_SHOTS, min(value, MAX_SHOTS_LIMIT))


def split_script(
    video_subject: str,
    video_script: str,
    *,
    max_shots: int = DEFAULT_MAX_SHOTS,
    attempts: int = DEFAULT_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> list[dict]:
    """把文案拆成 {text, term, title, brief} 分镜列表。

    失败抛 StoryboardError，调用方决定是降级还是让任务失败——渲染路径必须
    知道"分镜不可用"和"分镜为空"是两回事。
    """

    from app.services import llm as llm_service

    script = (video_script or "").strip()
    if not script:
        raise StoryboardError("文案为空")

    prompt = build_prompt(video_subject, script, normalize_max_shots(max_shots))
    response = ""
    last_error = ""
    for attempt in range(1, max(1, attempts) + 1):
        if attempt > 1 and retry_delay > 0:
            time.sleep(retry_delay)
        response = llm_service._generate_response(prompt)
        if not _is_error_response(response):
            break
        last_error = response[len(_ERROR_PREFIX):][:120]
        logger.warning(
            f"storyboard LLM attempt {attempt}/{max(1, attempts)} failed: "
            f"{(response or 'empty')[:120]}"
        )

    if _is_error_response(response):
        raise StoryboardError(f"分镜拆分失败，请检查 LLM 配置（{last_error}）")

    try:
        shots = parse_shots(response)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning(f"storyboard parse failed: {exc}; raw={response[:160]}")
        raise StoryboardError("分镜拆分结果无法解析，请重试一次") from exc

    if not shots:
        raise StoryboardError("分镜拆分为空")

    return shots