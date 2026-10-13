"""Smart material library: ingest pipeline, quality gates, auto-tags, matching.

上传的素材先由既有的 POST /api/v1/video_materials 落盘到素材目录；前端随后
调用本模块的入库接口启动后台流水线：

1. **拆条**：超过约 20 秒的随手拍长视频用 ffmpeg 场景检测切成 5~18 秒一段，
   并统一转成 ≤1080p、h264、去原声（一条 ffmpeg 完成，纯本机算力）；
2. **技术体检**：解码 96x96 灰度帧统计亮度/运动/清晰度，识别全黑、冻结帧、
   模糊等明显废片（零成本）；
3. **查重**：关键帧 dHash 感知哈希与全库比对（零成本）；
4. **AI 打标**（SiliconFlow VLM）：输出中文标签、英文别名字段，并顺带判定该
   段内容是否与美业相关（一次调用两个用途）；
5. **分诊**：入库 / 重复跳过 / 疑似无关（移入 .trash 隔离区） / 低质入库但降权；
   所有"跳过/拒绝"都保留文件在 .trash，用户可一键申诉入库。

库索引存 `config.toml [app].material_index`
（文件名 → {status, tags, tags_en, hashes, kind, duration, metrics}）。
关键词预检匹配与变体衍生（remix）同放本模块。上游文件只增加 router 注册行。
"""

import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.config import config as app_config
from app.config.config import (
    delete_config_nonblocking,
    try_save_config,
    update_config_nonblocking,
)
from app.models.const import FILE_TYPE_IMAGES, FILE_TYPE_VIDEOS
from app.utils import file_security, utils

_INDEX_KEY = "material_index"
_TRASH_SUBDIR = ".trash"

# 拆条参数
_SPLIT_SCENE_THRESHOLD = 0.30
_SEGMENT_MIN_SECONDS = 5.0
_SEGMENT_MAX_SECONDS = 18.0
_SPLIT_WHEN_LONGER_THAN = 20.0

# 体检阈值（96x96 灰度帧：均值亮度 / 相邻帧运动量 / 水平梯度方差）
_LUMA_BLACK = 14.0
_LUMA_DARK_WARN = 34.0
_MOTION_FROZEN = 0.35
_SHARP_LOW = 12.0

_DUP_HASH_DISTANCE = 6
_VISION_TIMEOUT = 60


# ===========================================================================
# 库索引存取
# ===========================================================================


def _material_dir(create: bool = True) -> str:
    return utils.storage_dir("local_videos", create=create)


def _trash_dir() -> str:
    path = os.path.join(_material_dir(), _TRASH_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def _resolved(filename: str) -> str:
    return file_security.resolve_path_within_directory(_material_dir(), filename)


def get_index() -> dict[str, dict]:
    raw = app_config.app.get(_INDEX_KEY) or {}
    return raw if isinstance(raw, dict) else {}


def _save_index(index: dict[str, dict]) -> None:
    update_config_nonblocking(app_config.app, _INDEX_KEY, index)
    try_save_config()


def _entry(filename: str) -> dict | None:
    entry = get_index().get(filename)
    return entry if isinstance(entry, dict) else None


def _upsert(filename: str, patch: dict) -> None:
    index = get_index()
    entry = dict(index.get(filename) or {})
    entry.update(patch)
    entry.setdefault("created_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    index[filename] = entry
    _save_index(index)


def _remove_entry(filename: str) -> None:
    index = get_index()
    if filename not in index:
        return
    index.pop(filename, None)
    if index:
        _save_index(index)
    else:
        delete_config_nonblocking(app_config.app, _INDEX_KEY)


def _kind_of(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in FILE_TYPE_VIDEOS:
        return "video"
    if ext in FILE_TYPE_IMAGES:
        return "image"
    return "other"


def list_library() -> dict:
    """列出素材目录实际存在的文件，并合并索引标签/状态。"""

    tags_map: dict[str, dict] = {}
    for name, entry in get_index().items():
        tags_map[name] = entry
    files = []
    root = Path(_material_dir())
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith(".") or not entry.is_file():
            continue
        ext = entry.suffix.lower().lstrip(".")
        if ext not in FILE_TYPE_VIDEOS and ext not in FILE_TYPE_IMAGES:
            continue
        index_entry = tags_map.get(entry.name) or {}
        files.append(
            {
                "file": entry.name,
                "kind": "video" if ext in FILE_TYPE_VIDEOS else "image",
                "size": entry.stat().st_size,
                "status": index_entry.get("status", "ok"),
                "tags": index_entry.get("tags", ["未打标"]),
                "tags_en": index_entry.get("tags_en", []),
                "duration": index_entry.get("duration"),
                "metrics": index_entry.get("metrics"),
                "created_at": index_entry.get("created_at"),
            }
        )
    return {"files": files}


def set_tags(filename: str, tags: list[str]) -> dict:
    entry = _entry(filename)
    if entry is None:
        raise ValueError(f"素材不存在或尚未入库: {filename}")
    cleaned = sorted({t.strip() for t in tags if t and t.strip()})[:8] or ["未打标"]
    entry["tags"] = cleaned
    _upsert(filename, entry)
    return {"file": filename, "tags": cleaned}


def delete_material(filename: str) -> bool:
    """删除素材：文件移入 .trash 隔离区（可人工找回），索引记录清除。"""

    resolved = _resolved(filename)
    source = Path(resolved)
    if source.is_file():
        shutil.move(str(source), str(Path(_trash_dir()) / filename))
    _remove_entry(filename)
    logger.info(f"material removed to trash: {filename}")
    return source.is_file()


def restore_material(filename: str) -> dict:
    """把 .trash 中被跳过/拒绝的素材找回入库（用户申诉通过）。"""

    trash_path = Path(_trash_dir()) / filename
    final_path = Path(_material_dir()) / filename
    if trash_path.is_file():
        shutil.move(str(trash_path), str(final_path))
    elif not final_path.is_file():
        raise ValueError("素材文件不存在")

    entry = _entry(filename) or {"kind": _kind_of(filename)}
    entry["status"] = "ok"
    tags = list(entry.get("tags") or [])
    if "已申诉入库" not in tags:
        tags.append("已申诉入库")
    entry["tags"] = tags
    _upsert(filename, entry)
    return entry


# ---- 素材管理页支撑：缩略图 / 预览 / 隔离区 ---------------------------------


_THUMBS_SUBDIR = ".thumbs"


def _thumbs_dir() -> Path:
    path = Path(_material_dir()) / _THUMBS_SUBDIR
    path.mkdir(exist_ok=True)
    return path


def material_file_path(filename: str) -> str:
    """素材目录内文件的绝对路径（供预览/缩略图接口使用）。"""

    return _resolved(filename)


def material_thumb_path(filename: str, max_age_hours: float = 24.0) -> str | None:
    """为视频/图片生成（或复用）一张 320px 宽的缩略图，返回绝对路径。"""

    resolved = Path(_resolved(filename))
    if not resolved.is_file():
        return None
    thumb_dir = _thumbs_dir()
    thumb_name = f"{Path(filename).stem}-{int(resolved.stat().st_mtime)}.jpg"
    thumb_path = thumb_dir / thumb_name
    existing = sorted(thumb_dir.glob(f"{Path(filename).stem}-*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    if existing:
        age_hours = (datetime.now().timestamp() - existing[0].stat().st_mtime) / 3600
        if _entry(filename) is not None and age_hours < max_age_hours:
            return str(existing[0])
        for stale in existing:
            try:
                stale.unlink()
            except OSError:
                pass
    if _kind_of(filename) == "image":
        if resolved.suffix.lower() in (".jpg", ".jpeg"):
            shutil.copy2(resolved, thumb_path)
            return str(thumb_path)
        _run(["-i", str(resolved), "-frames:v", "1", "-q:v", "5", str(thumb_path)])
    else:
        duration, _, _ = _probe_media(resolved)
        seek = min(1.5, duration / 3) if duration > 1 else 0
        _run(
            [
                "-ss", f"{seek:.2f}",
                "-i", str(resolved),
                "-frames:v", "1",
                "-vf", "scale=320:-2",
                "-q:v", "5",
                str(thumb_path),
            ]
        )
    return str(thumb_path) if thumb_path.is_file() and thumb_path.stat().st_size > 500 else None


def list_trash() -> dict:
    """隔离区视图：.trash 中仍存在的文件 + 入库时记录的判定原因。"""

    items = []
    root = Path(_trash_dir())
    for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        entry = _entry(path.name) or {}
        items.append(
            {
                "file": path.name,
                "kind": entry.get("kind") or _kind_of(path.name),
                "status": entry.get("status", "unknown"),
                "status_reason": entry.get("status_reason") or entry.get("desc") or "",
                "tags": entry.get("tags", []),
                "size": path.stat().st_size,
                "duration": entry.get("duration"),
            }
        )
    return {"files": items}


def purge_material(filename: str) -> bool:
    """从 .trash 永久删除（连索引记录一起清）。"""

    trash_path = Path(_trash_dir()) / filename
    if trash_path.is_file():
        trash_path.unlink()
    _remove_entry(filename)
    logger.info(f"material purged: {filename}")
    return True


# ===========================================================================
# ffmpeg 基础工具
# ===========================================================================


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str], timeout: int = 240) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        timeout=timeout,
        # ffmpeg 输出可能混有本地码页字节：强制 UTF-8 + 宽松替换，避免
        # GBK 控制台解码线程在 Windows 上直接崩掉（stdout 变 None）。
        encoding="utf-8",
        errors="replace",
    )


def _probe_media(path: Path) -> tuple[float, int, int]:
    """(时长, 宽, 高)。正则从 ffmpeg 的流信息解析，避免引入 ffprobe。"""

    result = subprocess.run(
        [_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    stderr = result.stderr or ""
    duration = 0.0
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", stderr)
    if match:
        h, m, s = match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)
    dims_match = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})[\s,]", stderr)
    width, height = (int(dims_match.group(1)), int(dims_match.group(2))) if dims_match else (1080, 1920)
    return duration, width, height


def _scene_split_boundaries(path: Path, duration: float) -> list[float]:
    run = subprocess.run(
        [
            _ffmpeg_exe(),
            "-hide_banner",
            "-i", str(path),
            "-vf",
            f"select='gt(scene,{_SPLIT_SCENE_THRESHOLD})',showinfo",
            "-fps_mode", "vfr",
            "-an",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    times = [
        float(t)
        for t in re.findall(r"pts_time:([\d.]+)", run.stderr or "")
        if 0.8 < float(t) < duration - 1.5
    ]
    return sorted(set(times))


def _merge_boundaries(times: list[float], duration: float) -> list[tuple[float, float]]:
    """把切换点收敛为长度落在 [MIN, MAX] 的连续段。"""

    points = [0.0, *times, duration]
    segments: list[tuple[float, float]] = []
    start = 0.0
    for point in points[1:]:
        length = point - start
        if length >= _SEGMENT_MAX_SECONDS:
            segments.append((start, min(start + _SEGMENT_MAX_SECONDS, point)))
            start = segments[-1][1]
            if point - start < _SEGMENT_MIN_SECONDS:
                start = point
        if point - start >= max(_SEGMENT_MIN_SECONDS, (duration - point) and _SEGMENT_MIN_SECONDS):
            segments.append((start, point))
            start = point
    if not segments and duration > 1:
        segments.append((0.0, duration))
    return [(s, e) for s, e in segments if _SEGMENT_MIN_SECONDS * 0.6 <= e - s]


def _normalize_box(width: int, height: int) -> tuple[int, int]:
    cap = 1080
    scale = min(1.0, cap / max(width, height)) if max(width, height) > cap else 1.0
    out_w = max(2, int(width * scale) // 2 * 2)
    out_h = max(2, int(height * scale) // 2 * 2)
    return out_w, out_h


# ===========================================================================
# 体检验帧（一次解码多用途：亮度/运动/清晰度/dHash）
# ===========================================================================

_GRAY_SIZE = 96


def _sample_gray_frames(path: Path, out_frames: list) -> None:
    # 灰度帧是二进制数据，必须按字节捕获（text 模式会把 stdout 解码成 None）。
    run = subprocess.run(
        [
            _ffmpeg_exe(),
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(path),
            "-vf", f"fps=2,scale={_GRAY_SIZE}:{_GRAY_SIZE},format=gray",
            "-frames:v", "48",
            "-f", "rawvideo",
            "-",
        ],
        capture_output=True,
        timeout=120,
    )
    frame_bytes = _GRAY_SIZE * _GRAY_SIZE
    data = run.stdout
    if not data:
        logger.warning(
            f"gray sampling failed: rc={run.returncode}, "
            f"{(run.stderr or b'')[-200:]}"
        )
        return
    for offset in range(0, len(data) - frame_bytes + 1, frame_bytes):
        out_frames.append(data[offset: offset + frame_bytes])


def _dhash(frame: bytes) -> str:
    rows = []
    for r in range(8):
        rows.append([frame[(r * 11 + 8) * 96 + c * 11] for c in range(9)])
    bits = []
    for y, row in enumerate(rows[:8]):
        for x in range(8):
            bits.append(1 if row[x] <= row[x + 1] else 0)
    return "".join(str(b) for b in bits)


def _hamming(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


def _tech_metrics(frames: list[bytes]) -> dict:
    if not frames:
        return {"mean_luma": -1, "motion": -1, "sharpness": -1}
    luma = [sum(f) / len(f) for f in frames]
    motion = 0.0
    for a, b in zip(frames, frames[1:]):
        motion += sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    motion /= max(1, len(frames) - 1)
    grads: list[float] = []
    for f in frames[::4]:
        for r in range(40, 56):
            row = f[r * 96: (r + 1) * 96]
            grads.extend(abs(a - b) for a, b in zip(row, row[1:]))
    mean_g = sum(grads) / max(1, len(grads))
    sharpness = math.sqrt(sum((g - mean_g) ** 2 for g in grads) / max(1, len(grads)))
    return {
        "mean_luma": round(sum(luma) / len(luma), 1),
        "motion": round(motion, 2),
        "sharpness": round(sharpness, 1),
    }


def _tech_verdict(metrics: dict) -> tuple[str, str]:
    if metrics["mean_luma"] < _LUMA_BLACK:
        return "reject", "画面几乎全黑"
    if 0 <= metrics["motion"] < _MOTION_FROZEN:
        return "reject", "画面静止（疑似冻结或屏幕翻拍）"
    if 0 <= metrics["mean_luma"] < _LUMA_DARK_WARN:
        return "low_quality", "画面偏暗，建议补光重拍"
    if 0 <= metrics["sharpness"] < _SHARP_LOW:
        return "low_quality", "画面偏糊"
    return "ok", ""


# ===========================================================================
# SiliconFlow VLM 打标 + 相关性判定
# ===========================================================================


def _vision_model() -> str:
    # 上游目录会随时间调整（Qwen2.5-VL 已下架，Qwen3-VL 在售 2026-10），
    # 这里只做默认值，登录用户可在 config.toml [app].ingest_vision_model 覆盖。
    return str(
        app_config.app.get("ingest_vision_model") or "Qwen/Qwen3-VL-8B-Instruct"
    ).strip()


def _frames_to_jpegs(path: Path, count: int = 3) -> list[bytes]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        pattern = os.path.join(tmp, "f%02d.jpg")
        exec_source: list[str] = ["-i", str(path)]
        result = _run(
            [
                "-i", str(path),
                "-vf", "fps=1",
                "-frames:v", str(count),
                "-q:v", "7",
                pattern,
            ],
            timeout=120,
        )
        frames = []
        for index in range(count):
            candidate = Path(tmp) / f"f{index:02d}.jpg"
            if candidate.is_file():
                frames.append(candidate.read_bytes())
        if not frames and Path(path).is_file():
            # 图片素材：直接压缩成 jpg 供 VLM 处理
            direct = Path(tmp) / "img.jpg"
            _run(["-i", str(path), "-frames:v", "1", "-q:v", "7", str(direct)])
            frames = [direct.read_bytes()] if direct.is_file() else []
        return frames


def _vlm_analyze(frames: list[bytes]) -> dict:
    api_key = str(app_config.siliconflow.get("api_key", "") or "").strip()
    if not api_key or not frames:
        return {}

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "这些画面来自同一段视频素材。请只输出一个 JSON，不要任何其他文字，格式："
                '{"tags": ["中文内容标签，3~5个"], "tags_en": ["对应的英文标签"], '
                '"beauty_related": true或false, "desc": "一句话内容描述", '
                '"reject": false, "reject_reason": ""}'
                '。如果画面明显与美业无关（屏幕翻拍、办公文档、代码、游戏、宠物、风景等），'
                '将 reject 设为 true 并在 reject_reason 说明。'
            ),
        }
    ]
    for frame in frames:
        encoded = base64.b64encode(frame).decode()
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
        )

    import requests

    try:
        response = requests.post(
            "https://api.siliconflow.cn/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": _vision_model(),
                "temperature": 0.2,
                "max_tokens": 500,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=_VISION_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning(f"vlm tagging request failed: {type(exc).__name__}")
        return {}
    if response.status_code != 200:
        logger.warning(f"vlm tagging failed: HTTP {response.status_code}")
        return {}
    try:
        text = response.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(match.group(0)) if match else {}
    except Exception as exc:  # noqa: BLE001 - 模型输出不规范时按"未打标"处理
        logger.warning(f"vlm tagging parse failed: {exc}")
        return {}


# ===========================================================================
# 入库流水线（后台作业）
# ===========================================================================

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def start_ingest(filename: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "done": False,
            "filename": filename,
            "events": [],
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    threading.Thread(
        target=_ingest_worker,
        args=(job_id, filename),
        daemon=True,
        name=f"ingest-{job_id}",
    ).start()
    return job_id


def get_ingest(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        snapshot = dict(job)
        snapshot["events"] = list(job["events"])
        return snapshot


def _add_event(job_id: str, event: dict) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["events"].append(event)


def _finish_ingest(job_id: str, summary: dict) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["done"] = True
            job["_ts"] = datetime.now(timezone.utc).timestamp()
            job["summary"] = summary
    _cleanup_jobs()


def _cleanup_jobs() -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - 1800
    with _JOBS_LOCK:
        for jid in [
            jid
            for jid, job in _JOBS.items()
            if job.get("done") and (job.get("_ts") or 0) < cutoff
        ]:
            _JOBS.pop(jid, None)


def _staggered_name(filename: str, index: int) -> str:
    """段文件名保持与原素材同前缀，便于用户追溯来源。"""

    stem = Path(filename).stem
    suffix = ".mp4"
    return f"{stem}-seg{index:02d}{suffix}"


def _ingest_worker(job_id: str, filename: str) -> None:
    events: list[dict] = []
    counts = {
        "accepted": 0, "skipped": 0, "low_quality": 0,
        "rejected_irrelevant": 0, "error": 0,
    }
    original = None
    try:
        original = Path(_resolved(filename))
        kind = _kind_of(filename)
        pieces: list[tuple[Path | None, float | None]] = []

        if kind == "video":
            duration, width, height = _probe_media(original)
            if width == 0 or duration <= 0.5:
                raise ValueError("无法读取的视频文件")
            out_w, out_h = _normalize_box(width, height)
            logger.info(
                f"ingest start: {filename}, duration={duration:.1f}s, "
                f"{width}x{height}, pieces-dir={out_w}x{out_h}"
            )
            if duration <= _SPLIT_WHEN_LONGER_THAN + 2.0:
                pieces = [(None, duration)]
            else:
                boundaries = _scene_split_boundaries(original, duration)
                segments = _merge_boundaries(boundaries, duration)
                pieces = [(None, f"{s:.2f}|{e:.2f}") for s, e in segments]
                logger.info(f"scene split: {len(segments)} segments")
        elif kind == "image":
            pieces = [(None, None)]
        else:
            raise ValueError("不支持的素材类型")

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for index, (_, span) in enumerate(pieces, 1):
                seg_name = _staggered_name(filename, index)
                is_image = kind == "image"
                tmp_seg = tmp_path / (
                    f"work{index}{Path(filename).suffix.lower() if is_image else '.mp4'}"
                )

                if is_image:
                    shutil.copy2(original, tmp_seg)
                else:
                    start_s, end_s = (0.0, min(duration, _SEGMENT_MAX_SECONDS)) if span is None or "|" not in span \
                        else (float(span.split("|")[0]), float(span.split("|")[1]))
                    take = max(0.8, min(_SEGMENT_MAX_SECONDS, end_s - start_s))
                    normalized = (
                        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2"
                    )
                    result = _run(
                        [
                            "-ss", f"{start_s:.2f}",
                            "-t", f"{take:.2f}",
                            "-i", str(original),
                            "-vf", normalized,
                            "-r", "25",
                            "-c:v", "libx264",
                            "-preset", "veryfast",
                            "-crf", "23",
                            "-pix_fmt", "yuv420p",
                            "-an",
                            str(tmp_seg),
                        ],
                        timeout=600,
                    )
                    if result.returncode != 0 or not tmp_seg.is_file() or tmp_seg.stat().st_size < 10_000:
                        counts["error"] += 1
                        _add_event(job_id, {
                            "file": seg_name, "status": "error",
                            "reason": f"转码失败: {result.stderr[-120:]}",
                        })
                        continue

                # --- 技术体检 ---
                frames: list[bytes] = []
                _sample_gray_frames(tmp_seg, frames)
                metrics = _tech_metrics(frames)
                tech_status, tech_reason = _tech_verdict(metrics)
                hashes = [_dhash(f) for f in frames[:: max(1, len(frames) // 3)][:3]]

                # --- 查重 ---
                duplicate_of: str | None = None
                if tech_status != "reject":
                    filename_padded = seg_name
                    for existing_name, existing in get_index().items():
                        if existing_name == filename_padded:
                            continue
                        if existing.get("status") not in ("ok", "low_quality"):
                            continue
                        for existing_hash in existing.get("hashes") or []:
                            for my_hash in hashes:
                                if my_hash and existing_hash and _hamming(my_hash, existing_hash) <= _DUP_HASH_DISTANCE:
                                    duplicate_of = existing_name
                                    break
                            if duplicate_of:
                                break
                        if duplicate_of:
                            break

                # --- AI 打标 ---
                jpegs = _frames_to_jpegs(tmp_seg, 3)
                vision = _vlm_analyze(jpegs)
                tags = [t.strip() for t in (vision.get("tags") or []) if t.strip()]
                tags_en = [t.strip() for t in (vision.get("tags_en") or []) if t.strip()]
                relevant = bool(vision.get("beauty_related", True))
                reject_reason = str(vision.get("reject_reason") or "").strip()

                # --- 分诊 ---
                if tech_status == "reject":
                    shutil.move(str(tmp_seg), str(Path(_trash_dir()) / seg_name))
                    counts["rejected_irrelevant"] += 1
                    _upsert(seg_name, {
                        "status": "reject",
                        "status_reason": tech_reason,
                        "tags": ["\u672a\u6253\u6807"],
                        "kind": kind,
                        "hashes": hashes,
                    })
                    _add_event(job_id, {
                        "file": seg_name, "status": "rejected",
                        "reason": tech_reason,
                        "restorable": False,
                    })
                    continue

                if duplicate_of:
                    shutil.move(str(tmp_seg), str(Path(_trash_dir()) / seg_name))
                    counts["skipped"] += 1
                    _upsert(seg_name, {
                        "status": "duplicate",
                        "status_reason": f"与库内【{duplicate_of}】重复或几乎相同",
                        "tags": ["\u672a\u6253\u6807"],
                        "kind": kind,
                        "hashes": hashes,
                    })
                    _add_event(job_id, {
                        "file": seg_name, "status": "duplicate",
                        "reason": f"与库内《{duplicate_of}》重复或几乎相同",
                        "restorable": True,
                    })
                    continue

                if not relevant and reject_reason:
                    shutil.move(str(tmp_seg), str(Path(_trash_dir()) / seg_name))
                    counts["rejected_irrelevant"] += 1
                    _upsert(seg_name, {
                        "status": "irrelevant",
                        "status_reason": f"疑似与美业无关：{reject_reason}",
                        "tags": tags or ["\u672a\u6253\u6807"],
                        "tags_en": tags_en[:6],
                        "kind": kind,
                        "hashes": hashes,
                    })
                    _add_event(job_id, {
                        "file": seg_name, "status": "irrelevant",
                        "reason": f"疑似与美业无关：{reject_reason}",
                        "restorable": True,
                    })
                    continue

                final_path = Path(_material_dir()) / seg_name
                shutil.move(str(tmp_seg), str(final_path))

                status = tech_status  # ok | low_quality
                if status == "low_quality":
                    counts["low_quality"] += 1
                    reason = tech_reason or "质量一般"
                else:
                    counts["accepted"] += 1
                    reason = "已自动打标入库"
                if not tags:
                    tags = ["未打标"]
                _upsert(seg_name, {
                    "status": status,
                    "status_reason": reason,
                    "tags": sorted(set(tags))[:6],
                    "tags_en": tags_en[:6],
                    "hashes": hashes,
                    "kind": kind if is_image else "video",
                    "duration": round(_probe_media(final_path)[0], 1) if kind == "video" else None,
                    "metrics": metrics,
                    "beauty_related": relevant,
                    "from": filename,
                })
                logger.info(f"ingested: {seg_name} ({status}) tags={tags[:3]}")

                if status == "ok":
                    # 拍摄需求自动核销：标签命中未补齐需求即标记 resolved
                    from app.services import shoot_needs as _shoot_needs

                    _shoot_needs.resolve_by_tags(
                        sorted(set(tags))[:6], tags_en[:6], seg_name
                    )

        # 处理完成的原始文件移入 .trash 留底，库目录只保留拆好的段
        if original and original.is_file() and counts["accepted"] + counts["skipped"] > 0:
            shutil.move(str(original), str(Path(_trash_dir()) / filename))

        _add_event(job_id, {"type": "summary", **counts})
        _finish_ingest(job_id, counts)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"ingest failed: {filename}: {exc}")
        _add_event(job_id, {"file": filename, "status": "error", "reason": str(exc)[:200]})
        counts["error"] += 1
        _finish_ingest(job_id, counts)


# ===========================================================================
# 关键词预检匹配
# ===========================================================================


def _entry_tags(entry: dict, filename: str) -> str:
    texts: list[str] = []
    for t in (entry.get("tags") or []):
        texts.append(str(t))
    for t in (entry.get("tags_en") or []):
        texts.append(str(t))
    texts.append(Path(filename).stem)
    return " ".join(texts).lower()


def match_terms(video_terms: list[str]) -> dict:
    covered: list[str] = []
    missing: list[str] = []
    matched: dict[str, list[str]] = {}
    for term in video_terms:
        if not term.strip():
            continue
        low = term.lower()
        hits: list[str] = []
        for filename, entry in get_index().items():
            if entry.get("status") not in ("ok", "low_quality"):
                continue
            haystack = _entry_tags(entry, filename)
            if low in haystack or any(low in t.lower() or t.lower() in low for t in (entry.get("tags") or [])):
                hits.append(filename)
        if hits:
            covered.append(term)
            matched[term] = hits[:6]
        else:
            missing.append(term)
    return {"covered": covered, "missing": missing, "matched": matched}


# ===========================================================================
# 变体衍生（零 AI 成本，纯 ffmpeg）
# ===========================================================================

_VIDEO_TRANSFORMS = ("mirror", "slow", "fast", "warm", "zoom")


def _term_transform(term: str) -> str:
    digest = hashlib.md5(term.encode("utf-8")).hexdigest()
    return _VIDEO_TRANSFORMS[int(digest[0], 16) % len(_VIDEO_TRANSFORMS)]


def _aspect_output_size(aspect: str) -> tuple[int, int]:
    aspect = (aspect or "9:16").strip()
    if aspect == "16:9":
        return 1920, 1080
    if aspect == "1:1":
        return 1080, 1080
    return 1080, 1920


def _remix_from_video(source: Path, out_path: Path, transform: str, size: tuple[int, int]) -> None:
    duration, _, _ = _probe_media(source)
    if duration <= 1.2:
        raise ValueError("source too short")
    take = min(8.0, max(2.0, duration * 0.5))
    start = max(0.0, (duration - take) / 2)
    width, height = size
    filters = {
        "mirror": "hflip",
        "slow": "setpts=1.18*PTS",
        "fast": "setpts=0.85*PTS",
        "warm": "eq=saturation=1.22:gamma_r=1.06:gamma_b=0.96",
        "zoom": f"scale={int(width * 1.12)}:{int(height * 1.12)},crop={width}:{height}",
    }
    vf = filters.get(transform, "hflip")
    normalize = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    chain = vf if transform == "zoom" else f"{normalize},{vf}"
    _run(
        [
            "-ss", f"{start:.2f}", "-i", str(source), "-t", f"{take:.2f}",
            "-vf", chain, "-r", "25",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", str(out_path),
        ],
        timeout=600,
    )
    if not out_path.is_file() or out_path.stat().st_size < 10_000:
        raise ValueError("remix output invalid")


def _remix_from_image(source: Path, out_path: Path, size: tuple[int, int]) -> None:
    width, height = size
    _run(
        [
            "-loop", "1", "-i", str(source), "-t", "6",
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                f"zoompan=z='min(zoom+0.0008,1.12)':d=150:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height},"
                f"fps=25"
            ),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", str(out_path),
        ],
        timeout=600,
    )
    if not out_path.is_file() or out_path.stat().st_size < 10_000:
        raise ValueError("remix output invalid")


def remix_for_missing_terms(missing_terms: list[str], aspect: str = "9:16") -> dict:
    """为缺失关键词从库内素材衍生变体；变体继承该词标签并入库。"""

    size = _aspect_output_size(aspect)
    root = Path(_material_dir())
    sources: list[Path] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.suffix.lower().lstrip(".") in FILE_TYPE_VIDEOS:
            sources.append(entry)
    images = [p for p in root.iterdir() if p.suffix.lower().lstrip(".") in FILE_TYPE_IMAGES]
    if not sources or not images:
        sources = sources or []
        sources.extend(images)
    if not sources:
        return {"created": [], "failed": [], "reason": "库内暂无可用于衍生的素材"}

    created: list[dict] = []
    failed: list[dict] = []
    for index, term in enumerate(missing_terms):
        if not term.strip():
            continue
        source = sources[index % len(sources)]
        transform = _term_transform(term)
        digest = hashlib.md5(term.encode("utf-8")).hexdigest()[:8]
        out_name = f"remix-{digest}.mp4"
        out_path = root / out_name
        try:
            if Path(source).suffix.lower().lstrip(".") in FILE_TYPE_VIDEOS and _probe_media(source)[0] > 1.2:
                _remix_from_video(source, out_path, transform, size)
                transform_label = transform
            else:
                _remix_from_image(source, out_path, size)
                transform_label = "kenburns"
            tags = sorted({*get_index().get(out_name, {}).get("tags", []), term})
            _upsert(out_name, {
                "status": "ok",
                "status_reason": f"由《{source.name}》{transform_label} 衍生",
                "tags": tags[:6],
                "kind": "video",
                "duration": round(_probe_media(out_path)[0], 1),
                "from": source.name,
            })
            created.append(
                {"file": out_name, "term": term, "from": source.name, "transform": transform_label}
            )
            logger.info(f"material remix: {out_name} <- {source.name} ({transform_label})")
        except Exception as exc:  # noqa: BLE001 - 单个失败不影响整批
            failed.append({"term": term, "error": str(exc)[:200]})
            logger.warning(f"material remix failed for {term!r}: {exc}")
    return {"created": created, "failed": failed}
