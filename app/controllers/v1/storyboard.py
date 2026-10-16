"""Storyboard extraction: split the script into shot-aligned (text, term) pairs.

分镜对齐功能的 HTTP 入口：提示词、解析与重试都实现在
`app/services/storyboard.py`，渲染流水线复用同一份实现按镜头规划画面，
本控制器只负责请求校验与 HTTP 语义。
"""

from fastapi import Depends, Path, Request
from pydantic import BaseModel

from app.controllers import base
from app.controllers.v1.base import new_router
from app.models.exception import HttpException
from app.services import pending_creations as pending_service
from app.services import shoot_needs as shoot_needs_service
from app.services import storyboard as storyboard_service
from app.utils import utils

router = new_router(dependencies=[Depends(base.verify_token)])


class StoryboardRequest(BaseModel):
    video_subject: str = ""
    video_script: str
    video_language: str = ""
    max_shots: int = storyboard_service.DEFAULT_MAX_SHOTS


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

    try:
        shots = storyboard_service.split_script(
            body.video_subject,
            script,
            max_shots=body.max_shots,
        )
    except storyboard_service.StoryboardError as exc:
        raise HttpException(
            task_id=request_id,
            status_code=503,
            message=f"{request_id}: {exc}",
        )

    return utils.get_response(200, {"shots": shots})


# ================= 拍摄需求（由视频生成任务产生的人话简报清单） =================


class ShootNeedItem(BaseModel):
    term: str = ""
    title: str = ""
    brief: str = ""


class ShootNeedsRequest(BaseModel):
    task_id: str
    items: list[ShootNeedItem] = []


class ShootNeedResolveRequest(BaseModel):
    id: str
    filename: str = ""


@router.post("/shoot_needs", summary="Register shoot needs (material gaps) for a task")
def register_shoot_needs(request: Request, body: ShootNeedsRequest):
    added = shoot_needs_service.add_for_task(
        body.task_id,
        [item.model_dump() for item in body.items],
    )
    return utils.get_response(200, {"added": added})


@router.get("/shoot_needs", summary="List shoot needs (all tasks, or filter by task_id)")
def list_shoot_needs(request: Request, task_id: str | None = None):
    # 懒清理：绑定的视频任务已删除/取消的需求自动丢弃，避免孤儿挂列表
    shoot_needs_service.prune_orphans()
    if task_id:
        needs = shoot_needs_service.needs_for_task(task_id)
    else:
        needs = shoot_needs_service.needs_for_task(None)
    return utils.get_response(200, {"needs": needs})


@router.get("/shoot_needs/open", summary="List open shoot needs across all tasks")
def list_open_shoot_needs(request: Request):
    shoot_needs_service.prune_orphans()
    return utils.get_response(200, {"needs": shoot_needs_service.aggregate_open()})


@router.delete("/shoot_needs/{need_id}", summary="Remove a shoot need (manual ignore)")
def remove_shoot_need(request: Request, need_id: str = Path(...)):
    shoot_needs_service.remove(need_id)
    return utils.get_response(200, {"removed": need_id})


@router.post("/shoot_needs/resolve", summary="Manually mark a shoot need as resolved")
def resolve_shoot_need(request: Request, body: ShootNeedResolveRequest):
    item = shoot_needs_service.resolve(body.id, body.filename)
    if not item:
        raise HttpException(
            task_id=base.get_task_id(request), status_code=404, message="需求不存在或已补齐"
        )
    return utils.get_response(200, {"need": item})


# ================= 等待素材的挂起创作（中断续跑） =================


class PendingCreationRequest(BaseModel):
    snapshot: dict
    terms: list[str] = []
    subject: str = ""


@router.post(
    "/pending_creations",
    summary="Park an interrupted creation waiting for library materials",
)
def add_pending_creation(request: Request, body: PendingCreationRequest):
    item = pending_service.add(body.snapshot, body.terms, body.subject)
    return utils.get_response(200, {"pending": item})


@router.get("/pending_creations", summary="List parked creations with live coverage")
def list_pending_creations(request: Request):
    return utils.get_response(200, {"pendings": pending_service.list_all()})


@router.delete(
    "/pending_creations/{pending_id}",
    summary="Cancel a parked creation",
)
def remove_pending_creation(request: Request, pending_id: str = Path(...)):
    pending_service.remove(pending_id)
    return utils.get_response(200, {"removed": pending_id})
