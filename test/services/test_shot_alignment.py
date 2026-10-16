"""分镜对齐的画面规划：顺序、时长、覆盖率与多样性。

这些用例锁住的是实测踩过的坑：整库顺序拼接导致画面与旁白无关、
素材不够时同一条 5 秒片段被循环播放 4 次。
"""

import pytest

import os

from app.services.shot_alignment import (
    MaterialCandidate,
    estimated_repeat_count,
    build_clip_plan,
    normalize_text,
    parse_srt_cues,
    score_candidate,
    shot_windows,
    source_name_of,
)


def _candidate(name, duration, tags, kind="video", tags_en=()):
    return MaterialCandidate(
        path=f"/library/{name}",
        name=name,
        kind=kind,
        duration=duration,
        tags=tuple(tags),
        tags_en=tuple(tags_en),
    )


def _write_srt(tmp_path, lines):
    """lines = [(start, end, text)]，写出标准 SRT。"""

    def stamp(value):
        hours, remainder = divmod(value, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d},{int(round((seconds % 1) * 1000)):03d}"

    blocks = []
    for index, (start, end, text) in enumerate(lines, 1):
        blocks.append(f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}\n")
    path = tmp_path / "subtitle.srt"
    path.write_text("\n".join(blocks), encoding="utf-8")
    return str(path)


def test_normalize_text_drops_punctuation_and_case():
    assert normalize_text("面部按摩， 特写！") == "面部按摩特写"
    assert normalize_text("Hand Close-Up") == "handcloseup"


def test_parse_srt_cues_reads_times_and_text(tmp_path):
    path = _write_srt(
        tmp_path,
        [
            (0.0, 2.5, "一个人过得好不好"),
            (2.5, 5.25, "最先从手和指尖露出来"),
        ],
    )
    cues = parse_srt_cues(path)
    assert [(round(start, 2), round(end, 2)) for start, end, _ in cues] == [(0.0, 2.5), (2.5, 5.25)]
    assert cues[1][2] == "最先从手和指尖露出来"


def test_parse_srt_cues_missing_file_is_not_fatal(tmp_path):
    assert parse_srt_cues(str(tmp_path / "nope.srt")) == []
    assert parse_srt_cues("") == []


def test_shot_windows_follow_subtitle_timeline(tmp_path):
    path = _write_srt(
        tmp_path,
        [
            (0.0, 3.0, "很多客人刚坐下来时并不是为了惊艳谁"),
            (3.0, 6.0, "只是生活太满"),
            (6.0, 10.0, "想给自己一个被好好对待的瞬间"),
        ],
    )
    shots = [
        {"text": "很多客人刚坐下来时并不是为了惊艳谁，只是生活太满", "term": "顾客入座"},
        {"text": "想给自己一个被好好对待的瞬间", "term": "护理放松"},
    ]
    windows = shot_windows(shots, parse_srt_cues(path), audio_duration=10.0)
    assert windows == [(0.0, 6.0), (6.0, 10.0)]


def test_shot_windows_fall_back_to_equal_split_without_cues():
    shots = [{"text": "a", "term": "t1"}, {"text": "b", "term": "t2"}]
    assert shot_windows(shots, [], audio_duration=20.0) == [(0.0, 10.0), (10.0, 20.0)]


def test_shot_windows_last_shot_extends_to_audio_end(tmp_path):
    path = _write_srt(tmp_path, [(0.0, 2.0, "第一句"), (2.0, 4.0, "第二句")])
    shots = [{"text": "第一句", "term": "t1"}, {"text": "第二句", "term": "t2"}]
    windows = shot_windows(shots, parse_srt_cues(path), audio_duration=30.0)
    assert windows[1][1] == 30.0


def test_score_candidate_prefers_direct_tag_hit():
    exact = _candidate("a.mp4", 10, ["美甲", "手部特写"])
    partial = _candidate("b.mp4", 10, ["美甲护理细节", "手部"])
    unrelated = _candidate("c.mp4", 10, ["厨房", "烹饪"])
    assert score_candidate("美甲", exact) > score_candidate("美甲", partial)
    assert score_candidate("美甲", partial) > 0
    # 宁缺毋滥：既没有该词、也不共享词的素材不算命中
    assert score_candidate("美甲", unrelated) == 0


def test_score_candidate_ignores_single_character_tags():
    loose = _candidate("a.mp4", 10, ["美"])
    assert score_candidate("美容", loose) == 0


def test_score_candidate_matches_english_aliases():
    candidate = _candidate("a.mp4", 10, ["睫毛"], tags_en=("eyelash_extension",))
    assert score_candidate("eyelash extension", candidate) > 0


def test_source_name_of_recovers_zoom_artifact():
    assert (
        source_name_of("/library/43f0e193-seg01.jpg.zoom-ef2d127de37b942b.mp4")
        == "43f0e193-seg01.jpg"
    )
    assert source_name_of("/library/plain.mp4") == "plain.mp4"


def test_plan_orders_materials_by_shot_and_covers_audio():
    shots = [
        {"text": "开场", "term": "手部特写", "title": "手部特写", "brief": ""},
        {"text": "过程", "term": "美睫操作", "title": "美睫操作", "brief": ""},
    ]
    candidates = [
        _candidate("lash.mp4", 20, ["美睫", "美容师"]),
        _candidate("hand.mp4", 20, ["手部特写", "美甲"]),
    ]
    plan = build_clip_plan(
        shots=shots,
        candidates=candidates,
        audio_duration=20.0,
        required_duration=20.1,
        max_clip_duration=5.0,
    )
    by_shot: dict[int, set[str]] = {}
    for clip in plan.clips:
        by_shot.setdefault(clip.shot_index, set()).add(clip.file_path)
    # 第一个镜头只用手部素材，第二个镜头只用美睫素材——顺序跟着文案走
    assert by_shot[0] == {"/library/hand.mp4"}
    assert by_shot[1] == {"/library/lash.mp4"}
    # 计划要盖满"音频 + 安全余量"，否则 combine_videos 会再循环补一条片段，
    # 结尾会出现一次莫名其妙的重复画面。
    assert plan.planned_duration == pytest.approx(20.1)
    assert plan.shortfall == 0
    assert estimated_repeat_count(plan, max_clip_duration=5.0) == 0
    assert plan.unmatched_shots == []


def test_plan_avoids_adjacent_repeats_of_one_source():
    """素材总时长刚好够：不能靠反复切同一条来填满（否则同款画面连播）。"""

    shots = [
        {"text": f"第{i}句", "term": "美甲", "title": "", "brief": ""} for i in range(1, 7)
    ]
    plan = build_clip_plan(
        shots=shots,
        candidates=[
            _candidate("a.mp4", 15, ["美甲", "手部特写"]),
            _candidate("b.mp4", 15, ["美甲师", "操作过程"]),
        ],
        audio_duration=30.0,
        required_duration=30.1,
        max_clip_duration=5.0,
    )
    sources = [os.path.basename(clip.file_path) for clip in plan.clips]
    assert all(first != second for first, second in zip(sources, sources[1:])), sources
    assert len(set(sources)) >= 2


def test_plan_respects_shot_length_from_subtitles(tmp_path):
    """第一个镜头 8 秒、第二个 2 秒：素材按镜头时长切，不做等分。"""

    path = _write_srt(
        tmp_path,
        [
            (0.0, 8.0, "先讲手指的细节"),
            (8.0, 10.0, "再讲整体"),
        ],
    )
    shots = [
        {"text": "先讲手指的细节", "term": "手部特写", "title": "", "brief": ""},
        {"text": "再讲整体", "term": "产品展示", "title": "", "brief": ""},
    ]
    plan = build_clip_plan(
        shots=shots,
        candidates=[
            _candidate("hand.mp4", 20, ["手部特写"]),
            _candidate("room.mp4", 20, ["产品展示"]),
        ],
        audio_duration=10.0,
        required_duration=10.1,
        max_clip_duration=5.0,
        cues=parse_srt_cues(path),
    )
    # 8 秒镜头 = 5s + 3s 两段，都来自手部素材；2 秒镜头来自产品展示素材
    shot_zero = [clip for clip in plan.clips if clip.shot_index == 0]
    shot_one = [clip for clip in plan.clips if clip.shot_index == 1]
    assert [round(clip.target_duration, 1) for clip in shot_zero] == [5.0, 3.0]
    assert {clip.file_path for clip in shot_zero} == {"/library/hand.mp4"}
    # 末尾多出的 0.1s 安全余量加在最后一段上，而不是再排一条片段
    assert [round(clip.target_duration, 1) for clip in shot_one] == [2.1]
    assert shot_one[0].file_path.endswith("room.mp4")
    assert plan.shortfall == 0


def test_plan_records_gap_when_no_material_matches():
    shots = [{"text": "结尾", "term": "海边日落", "title": "海边日落", "brief": "黄昏海边空镜"}]
    plan = build_clip_plan(
        shots=shots,
        candidates=[_candidate("hand.mp4", 30, ["手部特写"])],
        audio_duration=10.0,
        required_duration=10.1,
        max_clip_duration=5.0,
    )
    assert len(plan.unmatched_shots) == 1
    gap = plan.unmatched_shots[0]
    assert gap["term"] == "海边日落"
    assert gap["brief"] == "黄昏海边空镜"
    # 宁缺毋滥但不留空白：仍然排了兜底画面
    assert plan.clips and plan.clips[0].off_term is True


def test_plan_avoids_same_visual_family_when_alternatives_exist():
    """三张几乎同款的美甲特写 + 一条真视频：同一条成片里只出一张特写。"""

    shots = [
        {"text": f"第{i}句", "term": "美甲", "title": "", "brief": ""}
        for i in range(1, 5)
    ]
    candidates = [
        _candidate("nail-a.jpg", 5, ["美甲", "法式美甲", "手部特写"], kind="image"),
        _candidate("nail-b.jpg", 5, ["美甲", "法式美甲", "手部特写"], kind="image"),
        _candidate("nail-c.jpg", 5, ["美甲", "法式美甲", "手部特写"], kind="image"),
        _candidate("working.mp4", 30, ["美甲师", "操作过程"]),
    ]
    plan = build_clip_plan(
        shots=shots,
        candidates=candidates,
        audio_duration=20.0,
        required_duration=20.1,
        max_clip_duration=5.0,
    )
    # 有风格不同的替代素材时，第二镜头必须换风格，而不是把同款特写再播一遍
    assert plan.clips[0].file_path.endswith("nail-a.jpg")
    assert plan.clips[1].file_path.endswith("working.mp4")
    # 素材不够时宁可继续用同款，也不要留空白；同款出现次数因此受限
    nail_clips = [clip for clip in plan.clips if clip.file_path.endswith("nail-")]
    assert len(nail_clips) < len(plan.clips)


def test_plan_reports_shortfall_when_library_cannot_cover_audio():
    shots = [{"text": f"第{i}句", "term": "美甲", "title": "", "brief": ""} for i in range(1, 5)]
    plan = build_clip_plan(
        shots=shots,
        candidates=[_candidate("only.mp4", 6, ["美甲"])],
        audio_duration=40.0,
        required_duration=40.1,
        max_clip_duration=5.0,
    )
    assert plan.shortfall > 0
    consumed_source = sum(clip.end_time - clip.start_time for clip in plan.clips)
    # 同一条素材最多完整走两遍（6s x 2），不会为了铺满 40 秒反复循环同一段
    assert consumed_source <= 12.0
    assert plan.planned_duration < 40.1
    assert estimated_repeat_count(plan, max_clip_duration=5.0) > 0


def test_plan_variant_rotates_candidate_priority():
    shots = [{"text": "开场", "term": "美甲", "title": "", "brief": ""}]
    candidates = [
        _candidate("a.mp4", 5, ["美甲", "手部"]),
        _candidate("b.mp4", 5, ["美甲", "面部"]),
    ]
    first = build_clip_plan(
        shots=shots, candidates=candidates, audio_duration=5.0, required_duration=5.1
    )
    second = build_clip_plan(
        shots=shots, candidates=candidates, audio_duration=5.0, required_duration=5.1, variant=2
    )
    assert first.clips[0].file_path != second.clips[0].file_path


def test_plan_is_empty_without_candidates():
    plan = build_clip_plan(
        shots=[{"text": "a", "term": "t"}],
        candidates=[],
        audio_duration=10.0,
        required_duration=10.1,
    )
    assert plan.clips == []


def test_plan_serializes_for_combine_videos():
    plan = build_clip_plan(
        shots=[{"text": "a", "term": "美甲", "title": "", "brief": ""}],
        candidates=[_candidate("a.mp4", 10, ["美甲"])],
        audio_duration=5.0,
        required_duration=5.1,
        max_clip_duration=5.0,
    )
    payload = plan.as_dicts()
    assert payload[0]["file_path"] == "/library/a.mp4"
    assert payload[0]["end_time"] - payload[0]["start_time"] == pytest.approx(
        payload[0]["target_duration"]
    )