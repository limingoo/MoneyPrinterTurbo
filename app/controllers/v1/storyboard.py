"""Storyboard extraction: split the script into shot-aligned (text, term) pairs.

分镜对齐功能的后端部分：把整段文案交给当前 LLM 提供商，拆成若干分镜
（句子原文 + 为该句挑选画面的简短中文关键词）。前端拿到分镜表后：

* 网络源 → 直接启用上游 match_materials_to_script 的文案顺序排序；
* 我的素材库 → 逐镜头按 term 匹配库内标签文件，按镜头顺序传给流水线。

仅复用 llm 服务现有的响应链（含 OpenCode Go 等运行时扩展的本会话头/路由），
输出做 JSON 容错解析；除 router 注册行外不触碰上游文件。
"""

import json
import re

from fastapi import Depends, Request
from pydantic import BaseModel

from app.controllers import base
from app.controllers.v1.base import new_router
from app.models.exception import HttpException
from app.services import llm as llm_service
from app.utils import utils

router = new_router(dependencies=[Depends(base.verify_token)])


class StoryboardRequest(BaseModel):
    video_subject: str = ""
    video_script: str
    video_language: str = ""
    max_shots: int = 10


def _build_prompt(subject: str, script: str, max_shots: int) -> str:
    return (
        "你是一名短视频分镜师。请把下面的视频文案拆分成若干分镜，用于按叙述顺序挑选画面素材。\n\n"
        "要求：\n"
        f"1. 分镜数量 3~{max_shots} 个，按文案叙述顺序排列；\n"
        "2. 每个分镜的 text 必须是文案中的**原句或原句合并**，不要改写、不要省略、合并后要能覆盖全部文案；\n"
        "3. 每个分镜的 term 是该句画面的中文关键词（2~6 个字，如：面部按摩、樱花特写），\n"
        "   要具体到可搜索画面，贴合主题；\n"
        "4. 只输出 JSON 数组，不要任何其他文字，格式：\n"
        '[{"text": "原句", "term": "画面关键词"}]\n\n'
        f"视频主题：{subject or '（从文案推断）'}\n"
        f"视频文案：\n{script}"
    )


def _parse_shots(text: str) -> list[dict]:
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
        if line and term:
            shots.append({"text": line, "term": term})
    return shots


@router.post("/storyboard", summary="Split the script into shot-aligned (text, term) pairs")
def make_storyboard(
    request: Request,
    body: StoryboardRequest,
):
    request_id = base.get_task_id(request)
    script = (body.video_script or "").strip()
    if not script:
        raise HttpException(
            task_id=request_id, status_code=400, message=f"{request_id}: 文案为空"
        )

    prompt = _build_prompt(body.video_subject, script, max(3, min(body.max_shots, 12)))

    # 走统一 LLM 响应链（含运行时 provider 扩展）；失败会以 "Error: ..." 字符串返回
    response = llm_service._generate_response(prompt)

    if not response or response.startswith("Error: "):
        raise HttpException(
            task_id=request_id,
            status_code=503,
            message=f"{request_id}: 分镜拆分失败，请检查 LLM 配置" + (
                f"（{response[7:120]}）" if response else ""
            ),
        )
    try:
        shots = _parse_shots(response)
    except (ValueError, json.JSONDecodeError) as exc:
        logger_shots = response[:160]
        utils.logger.warning(f"storyboard parse failed: {exc}; raw={logger_shots}")
        raise HttpException(
            task_id=request_id,
            status_code=503,
            message=f"{request_id}: 分镜拆分结果无法解析，请重试一次",
        )

    if not shots:
        raise HttpException(
            task_id=request_id, status_code=503, message=f"{request_id}: 分镜拆分为空"
        )

    return utils.get_response(200, {"shots": shots})
