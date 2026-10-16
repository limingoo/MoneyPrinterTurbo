"""本地素材源的分镜对齐接线：规划输入、缺口登记、计划落盘、失败降级。"""

import json
from types import SimpleNamespace

import pytest

from app.models.schema import VideoParams
from app.services import task as task_service
from app.services.shot_alignment import MaterialCandidate


def _params(**overrides):
    base = dict(
        video_source="local",
        video_subject="美甲店",
        video_script="文案",
        video_aspect="9:16",
        video_fit_mode="cover",
        video_concat_mode="sequential",
        video_clip_duration=5,
        video_clip_speed=1.0,
        match_materials_to_script=True,
        video_count=1,
        n_threads=2,
        bgm_type="random",
        bgm_file="",
        bgm_volume=0.2,
        voice_volume=1.0,
    )
    base.update(overrides)
    return VideoParams(**base)


def _library_index():
    return {
        "hand.mp4": {
            "status": "ok",
            "duration": 12.0,
            "tags": ["手部特写", "美甲"],
            "tags_en": ["hand_closeup"],
        },
        "lash.mp4": {
            "status": "ok",
            "duration": 8.0,
            "tags": ["美睫", "美容师"],
            "tags_en": ["eyelash"],
        },
    }


def _patch_storyboard(monkeypatch, shots):
    monkeypatch.setattr(
        task_service.storyboard_service, "split_script", lambda *a, **k: shots
    )


def test_build_plan_inputs_maps_library_tags(monkeypatch):
    _patch_storyboard(
        monkeypatch,
        [
            {"text": "手和指尖", "term": "手部特写", "title": "手部特写", "brief": ""},
            {"text": "美睫过程", "term": "美睫", "title": "美睫", "brief": ""},
        ],
    )
    monkeypatch.setattr(task_service.material_library, "get_index", _library_index)

    inputs = task_service.build_shot_plan_inputs(
        "task-1",
        _params(),
        "手和指尖。美睫过程。",
        "",
        12.0,
        ["/library/hand.mp4", "/library/lash.mp4"],
    )
    assert inputs is not None
    assert inputs["audio_duration"] == 12.0
    # 合成阶段的安全余量必须计入，否则 combine_videos 会再循环补一段
    assert inputs["required_duration"] == pytest.approx(12.1)
    assert [c.name for c in inputs["candidates"]] == ["hand.mp4", "lash.mp4"]
    assert inputs["candidates"][0].tags == ("手部特写", "美甲")
    assert inputs["candidates"][0].tags_en == ("hand_closeup",)
    assert inputs["candidates"][0].duration == 12.0


def test_build_plan_inputs_disabled_without_match_flag(monkeypatch):
    called = []
    _patch_storyboard(monkeypatch, called.append)  # 不应被调用
    assert (
        task_service.build_shot_plan_inputs(
            "task-2",
            _params(match_materials_to_script=False),
            "文案",
            "",
            10.0,
            ["/library/hand.mp4"],
        )
        is None
    )
    assert called == []


def test_build_plan_inputs_survives_storyboard_failure(monkeypatch):
    """分镜不可用时必须退回旧的固定步长拼接，而不是让任务失败。"""

    def boom(*args, **kwargs):
        raise task_service.storyboard_service.StoryboardError("网关超时")

    monkeypatch.setattr(task_service.storyboard_service, "split_script", boom)
    assert (
        task_service.build_shot_plan_inputs(
            "task-3", _params(), "文案", "", 10.0, ["/library/hand.mp4"]
        )
        is None
    )


def test_build_plan_inputs_ignores_empty_script():
    assert (
        task_service.build_shot_plan_inputs(
            "task-4", _params(), "", "", 10.0, ["/library/hand.mp4"]
        )
        is None
    )


def _stub_render(tmp_path, monkeypatch, recorded):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "script.json").write_text(json.dumps({"script": "文案"}), encoding="utf-8")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")

    def fake_combine(**kwargs):
        recorded.update(kwargs)
        combined = task_dir / "combined-1.mp4"
        combined.write_bytes(b"video")
        return str(combined)

    monkeypatch.setattr(task_service.video, "combine_videos", fake_combine)
    monkeypatch.setattr(
        task_service.video,
        "generate_video",
        lambda video_path, **kwargs: str(task_dir / "final-1.mp4"),
    )
    monkeypatch.setattr(task_service.utils, "task_dir", lambda task_id: str(task_dir))
    return task_dir, audio


def test_generate_final_videos_passes_plan_to_combine(tmp_path, monkeypatch):
    recorded: dict = {}
    task_dir, audio = _stub_render(tmp_path, monkeypatch, recorded)
    gaps: list = []
    monkeypatch.setattr(
        task_service, "_register_shot_gaps", lambda task_id, plan: gaps.append(plan)
    )

    plan_inputs = {
        "shots": [{"text": "开场", "term": "手部特写", "title": "手部特写", "brief": "手部近景"}],
        "candidates": [
            MaterialCandidate("/library/hand.mp4", "hand.mp4", "video", 12.0, ("手部特写",))
        ],
        "audio_duration": 10.0,
        "required_duration": 10.1,
        "max_clip_duration": 5.0,
        "clip_speed": 1.0,
        "cues": [],
    }
    task_service.generate_final_videos(
        "task",
        _params(),
        ["/library/hand.mp4"],
        str(audio),
        "",
        10.0,
        shot_plan_inputs=plan_inputs,
    )

    plan = recorded["clip_plan"]
    assert plan, "计划必须传给 combine_videos"
    assert all(entry["file_path"] == "/library/hand.mp4" for entry in plan)
    assert recorded["video_paths"] == ["/library/hand.mp4"]
    assert len(gaps) == 1  # 只调用一次缺口登记

    payload = json.loads((task_dir / "script.json").read_text(encoding="utf-8"))
    assert payload["shot_plan"][0]["items"][0]["term"] == "手部特写"
    assert payload["shot_plan"][0]["distinct_materials"] == 1


def test_generate_final_videos_without_plan_keeps_legacy_path(tmp_path, monkeypatch):
    recorded: dict = {}
    _, audio = _stub_render(tmp_path, monkeypatch, recorded)

    task_service.generate_final_videos(
        "task",
        _params(match_materials_to_script=False),
        ["/library/hand.mp4"],
        str(audio),
        "",
        10.0,
    )
    assert recorded["clip_plan"] is None
    assert recorded["video_paths"] == ["/library/hand.mp4"]


def test_register_shot_gaps_is_best_effort(monkeypatch):
    """拍摄需求登记失败不能影响成片。"""

    def boom(*args, **kwargs):
        raise RuntimeError("config 不可写")

    import app.services.shoot_needs as shoot_needs

    monkeypatch.setattr(shoot_needs, "add_for_task", boom)
    plan = SimpleNamespace(unmatched_shots=[{"term": "海边日落", "title": "", "brief": ""}])
    task_service._register_shot_gaps("task-x", plan)