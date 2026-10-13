"""Shoot-needs registry: per-task material gaps surfaced as human-readable briefs.

拍摄需求由视频生成任务产生：分镜对齐/预检发现某镜头在素材库里没有可用画面时，
以人话简报（title + brief，LLM 生成的拍摄说明）登记到任务下；素材库页呈现
跨任务聚合的"待拍摄"视图；素材入库打标后自动比对标签核销对应需求。

存储在 `config.toml [app].shoot_needs`，条目：
{ id, task_id, term, title, brief, status(open/resolved), filename, requested_at }
"""

import uuid
from datetime import datetime, timezone

from loguru import logger

from app.config import config as app_config
from app.config.config import (
    delete_config_nonblocking,
    try_save_config,
    update_config_nonblocking,
)

_KEY = "shoot_needs"


def _items() -> list[dict]:
    raw = app_config.app.get(_KEY) or []
    return [item for item in raw if isinstance(item, dict) and item.get("id")]


def _save(items: list[dict]) -> None:
    if items:
        update_config_nonblocking(app_config.app, _KEY, items)
    else:
        delete_config_nonblocking(app_config.app, _KEY)
    try_save_config()


def add_for_task(
    task_id: str,
    items: list[dict],
    *,
    replace_existing: bool = True,
) -> int:
    """登记某个生成任务的拍摄缺口；相同 (term, title) 只登记一次。"""

    existing = _items()
    if replace_existing:
        existing = [item for item in existing if item.get("task_id") != task_id]

    added = 0
    seen = {
        (item.get("term"), item.get("title"))
        for item in existing
        if item.get("status") == "open"
    }
    for source in items:
        term = str(source.get("term") or "").strip()
        title = str(source.get("title") or "").strip()
        brief = str(source.get("brief") or "").strip()
        if not title:
            title = term or "画面素材"
        if not term:
            term = title
        key = (term, title)
        if key in seen:
            continue
        seen.add(key)
        existing.append(
            {
                "id": uuid.uuid4().hex[:12],
                "task_id": task_id,
                "term": term,
                "title": title,
                "brief": brief,
                "status": "open",
                "filename": "",
                "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        added += 1
    _save(existing)
    logger.info(f"shoot needs registered for task {task_id}: +{added}")
    return added


def needs_for_task(task_id: str | None = None) -> list[dict]:
    items = _items()
    if task_id:
        return sorted(
            (item for item in items if item.get("task_id") == task_id),
            key=lambda i: i.get("requested_at") or "",
        )
    return sorted(items, key=lambda i: (i.get("status"), i.get("requested_at") or ""))


def aggregate_open() -> list[dict]:
    """所有未补齐的需求（跨任务），按登记时间新→旧。"""

    open_items = [item for item in _items() if item.get("status") == "open"]
    return sorted(open_items, key=lambda i: i.get("requested_at") or "", reverse=True)


def resolve(item_id: str, filename: str = "") -> dict | None:
    resolved = []
    result = None
    for item in _items():
        if item.get("id") == item_id and item.get("status") != "resolved":
            item["status"] = "resolved"
            item["filename"] = filename
            result = item
        resolved.append(item)
    _save(resolved)
    return result


def resolve_by_tags(tags: list, tags_en: list, filename: str) -> list[dict]:
    """入库素材打标后自动核销：标签命中 open 需求即标记 resolved。

    匹配规则（宽松包含任一命中即可）：拍摄简报 title/brief/term 与素材
    中文标签、英文标签做包含比对——"手法特写"能命中"面部按摩+手法特写"。
    """

    resolved: list[dict] = []
    haystack_parts = [str(t).lower() for t in tags if t] + [
        str(t).lower() for t in tags_en if t
    ]
    if not filename or not haystack_parts:
        return resolved

    items = _items()
    changed = False
    for item in items:
        if item.get("status") != "open":
            continue
        needles = [
            str(item.get(key) or "").lower()
            for key in ("term", "title", "brief")
            if item.get(key)
        ]
        hit = any(n in hay or hay in n for n in needles for hay in haystack_parts)
        if hit:
            item["status"] = "resolved"
            item["filename"] = filename
            changed = True
            resolved.append(item)
            logger.info(
                f"shoot need auto-resolved by ingest: need={item.get('title')!r}, file={filename}"
            )
    if changed:
        _save(items)
    return resolved


def remove(item_id: str) -> None:
    """移除一条需求（用户手动"忽略"）。"""

    remaining = [item for item in _items() if item.get("id") != item_id]
    if len(remaining) != len(_items()):
        _save(remaining)


_DRAFT_TASK_ID = "creation"  # 创作页提交被拦下（无任务创建的拍摄需求草稿）

def prune_orphans() -> None:
    """懒清理：绑定的视频任务已不存在（删除/取消）时，丢弃其未补齐需求。

    'creation' 草稿（无任务的创作登记）不受影响，仅由用户忽略或素材命中核销。
    """

    from app.services import state as sm

    items = _items()
    kept = []
    changed = False
    for item in items:
        task_id = item.get("task_id")
        if item.get("status") != "resolved" and task_id and task_id != _DRAFT_TASK_ID:
            # 物理查询任务是否存在：不在了就清掉（含 resolved 的一并丢弃——
            # 任务已消失，历史简报也不再有对象）
            if sm.state.get_task(task_id) is None:
                changed = True
                logger.info(f"shoot need pruned (task gone): {item.get('title')!r}")
                continue
        kept.append(item)
    if changed:
        _save(kept)


def remove_for_task(task_id: str) -> None:
    """任务删除时清理其未补齐需求（已 resolve 的历史保留）。"""

    remaining = [
        item
        for item in _items()
        if not (item.get("task_id") == task_id and item.get("status") == "open")
    ]
    if len(remaining) != len(_items()):
        _save(remaining)
