#!/usr/bin/env python3
"""从磁盘产物回填缺失的 state.json（供文件状态后端 FileState 使用）。

背景：在 `enable_redis = false` 且旧实现使用内存态时，重启 API 会清空任务列表。
本脚本扫描 `storage/tasks/<task_id>/script.json`，为缺少 `state.json` 的历史任务
尽力补回一个任务记录，使任务列表能重新显示这些任务。

判定规则（尽力而为，可能与真实历史不一致）：
- 任务目录中存在 `final-*.mp4`：视为已完成（state=1, progress=100）；
- 否则视为失败（state=-1），避免把未出片的任务当成成功。
`created_at` 取任务目录内最早产物的 mtime（Unix 秒），仅用于列表排序与展示。

默认只预览；确认无误后加 `--apply` 才写入 `state.json`。

用法：
    python3 scripts/backfill_task_state.py            # 预览
    python3 scripts/backfill_task_state.py --apply    # 写入
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DEFAULT_TASKS_DIR = os.path.join(ROOT, "storage", "tasks")

STATE_COMPLETE = 1
STATE_FAILED = -1
STATE_FILENAME = "state.json"


def _earliest_mtime(task_dir: str) -> int:
    mtimes = []
    for name in os.listdir(task_dir):
        if name.startswith("."):
            continue
        try:
            mtimes.append(os.path.getmtime(os.path.join(task_dir, name)))
        except OSError:
            continue
    return int(min(mtimes)) if mtimes else int(time.time())


def _rel_videos(task_id: str, task_dir: str, prefix: str) -> list[str]:
    # 存相对 storage/tasks 根目录的路径，避免写入宿主机绝对路径而在容器内失效。
    names = sorted(
        name
        for name in os.listdir(task_dir)
        if name.startswith(prefix) and name.endswith(".mp4")
    )
    # 存在但为空的文件不算产物
    return [
        f"{task_id}/{name}"
        for name in names
        if os.path.getsize(os.path.join(task_dir, name)) > 0
    ]


def build_state(task_id: str, task_dir: str) -> dict | None:
    script_path = os.path.join(task_dir, "script.json")
    if not os.path.exists(script_path):
        return None
    try:
        with open(script_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    final_videos = _rel_videos(task_id, task_dir, "final-")
    combined_videos = _rel_videos(task_id, task_dir, "combined-")

    audio_rel = f"{task_id}/audio.mp3"
    subtitle_rel = f"{task_id}/subtitle.srt"
    audio_path = os.path.join(task_dir, "audio.mp3")
    subtitle_path = os.path.join(task_dir, "subtitle.srt")

    record = {
        "task_id": task_id,
        "state": STATE_COMPLETE if final_videos else STATE_FAILED,
        "progress": 100 if final_videos else 0,
        "video_subject": str(params.get("video_subject") or "").strip(),
        "script": str(data.get("script") or ""),
        "videos": final_videos or None,
        "combined_videos": combined_videos or None,
        "audio_file": audio_rel if os.path.exists(audio_path) else None,
        "subtitle_path": subtitle_rel if os.path.exists(subtitle_path) else None,
        "created_at": _earliest_mtime(task_dir),
        "stage": "complete" if final_videos else "unavailable",
    }
    return {key: value for key, value in record.items() if value is not None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tasks-dir",
        default=DEFAULT_TASKS_DIR,
        help=f"任务目录（默认 {DEFAULT_TASKS_DIR}）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写入 state.json；默认仅预览",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.tasks_dir):
        print(f"任务目录不存在：{args.tasks_dir}", file=sys.stderr)
        return 1

    planned = 0
    skipped = 0
    for task_id in sorted(os.listdir(args.tasks_dir)):
        task_dir = os.path.join(args.tasks_dir, task_id)
        if not os.path.isdir(task_dir):
            continue
        state_path = os.path.join(task_dir, STATE_FILENAME)
        if os.path.exists(state_path):
            skipped += 1
            continue
        record = build_state(task_id, task_dir)
        if record is None:
            continue
        planned += 1
        status = "完成" if record["state"] == STATE_COMPLETE else "失败"
        subject = record.get("video_subject") or "(无主题)"
        print(f"[{status}] {task_id}  {subject[:40]}")
        if args.apply:
            tmp_path = state_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, default=str)
            os.replace(tmp_path, state_path)

    verb = "已写入" if args.apply else "待写入（预览）"
    print(f"\n{verb}：{planned} 个，跳过（已有 state.json）：{skipped} 个")
    if not args.apply and planned:
        print("确认无误后加 --apply 写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
