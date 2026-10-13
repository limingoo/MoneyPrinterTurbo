"""Pending creations: interrupted submissions waiting for library materials.

按下"生成"但素材库命中不全（部分/零命中）时，不出废片也不静默丢弃——
把当时的创作表单快照挂起为"等待素材"条目。用户照拍摄简报/关键词补素材后，
素材库聚合会随着条目状态自动转"就绪"，创作页一键恢复快照继续生成。

存储于 `config.toml [app].pending_creations`：
{ id, subject, snapshot, terms, created_at }
"""

import json
import uuid
from datetime import datetime, timezone

from loguru import logger

from app.config import config as app_config
from app.config.config import (
    delete_config_nonblocking,
    try_save_config,
    update_config_nonblocking,
)

_KEY = "pending_creations"


def _items() -> list[dict]:
    raw = app_config.app.get(_KEY) or []
    return [item for item in raw if isinstance(item, dict) and item.get("id")]


def _save(items: list[dict]) -> None:
    if items:
        update_config_nonblocking(app_config.app, _KEY, items)
    else:
        delete_config_nonblocking(app_config.app, _KEY)
    try_save_config()


def add(snapshot: dict, terms: list[str], subject: str = "") -> dict:
    snapshot_payload = dict(snapshot)
    snapshot_payload.pop("local_materials", None)  # 由命中情况在续跑时重算
    item = {
        "id": uuid.uuid4().hex[:12],
        "subject": subject or "未命名创作",
        "snapshot": json.dumps(snapshot_payload, ensure_ascii=False),
        "terms": [str(t) for t in terms if t],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    items = [x for x in _items() if x["snapshot"] != item["snapshot"]]  # 去重相同快照
    items.append(item)
    _save(items)
    logger.info(f"pending creation added: id={item['id']}")
    return item


def list_all() -> list[dict]:
    """全部挂起条目，带实时的关键词覆盖情况（covered/missing/ready）。"""

    from app.services import material_library as ml

    result = []
    for item in sorted(_items(), key=lambda x: x.get("created_at") or "", reverse=True):
        entry = dict(item)
        terms: list[str] = item.get("terms") or []
        if terms:
            try:
                match = ml.match_terms(terms)
                entry["covered"] = list(match.get("covered") or [])
                entry["missing"] = list(match.get("missing") or [])
            except Exception as exc:  # noqa: BLE001 - 覆盖检查失败不阻断展示
                logger.warning(f"pending coverage check failed: {exc}")
                entry["covered"], entry["missing"] = [], terms
        else:
            entry["covered"], entry["missing"] = [], []
        entry["ready"] = bool(terms) and not entry["missing"]
        result.append(entry)
    return result


def remove(item_id: str) -> None:
    remaining = [item for item in _items() if item.get("id") != item_id]
    if len(remaining) != len(_items()):
        _save(remaining)
