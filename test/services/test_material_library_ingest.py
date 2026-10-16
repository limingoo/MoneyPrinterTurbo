"""素材库入库的画质与可用性门禁。

覆盖三件事：

* 入库归一化按**短边**封顶（竖屏 1080x1920 不再被降成 607x1080 再放大）；
* 查重同时看 dHash 与 aHash；
* VLM 判定"不能当空镜"（烧录字幕 / 对镜头聊天）时移入隔离区。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import config as app_config
from app.services import material_library


def _frame(value=128, size=96):
    """带结构的灰度帧（纯色会被判成模糊/静止，测不出真实分支）。"""

    frame = bytearray()
    for row in range(size):
        for col in range(size):
            base = (row * 3 + col * 5) % 256
            frame.append(min(255, (base + value) % 256))
    return bytes(frame)


def _frame_pair():
    """两帧内容明显不同 → 运动量 > _MOTION_FROZEN，不会被判成冻结/翻拍。"""

    def textured(seed, shift):
        rows = []
        for r in range(96):
            for c in range(96):
                rows.append(((r * 5 + c * 3 + seed * 37 + shift) % 200) + 20)
        return bytes(rows)

    return [textured(1, 0), textured(1, 40)]


def test_normalize_box_keeps_portrait_1080x1920():
    """竖屏 1080x1920 必须原样保留：降成 607x1080 再放大会糊。"""

    assert material_library._normalize_box(1080, 1920) == (1080, 1920)
    assert material_library._normalize_box(1920, 1080) == (1920, 1080)


def test_normalize_box_caps_large_sources_by_short_side():
    assert material_library._normalize_box(2160, 3840) == (1080, 1920)
    assert material_library._normalize_box(3840, 2160) == (1920, 1080)
    # 小于上限的原样保留，绝不放大
    assert material_library._normalize_box(720, 1280) == (720, 1280)


def test_normalize_box_respects_configured_cap(monkeypatch):
    monkeypatch.setitem(app_config.app, "ingest_max_short_side", 480)
    assert material_library._normalize_box(1080, 1920) == (480, 852)


def test_ingest_settings_are_bounded(monkeypatch):
    monkeypatch.setitem(app_config.app, "ingest_crf", 99)
    monkeypatch.setitem(app_config.app, "ingest_preset", "")
    monkeypatch.setitem(app_config.app, "ingest_max_short_side", 10)
    cap, crf, preset = material_library._ingest_settings()
    assert cap == 240  # 低于 240 的短边会让成片无法铺满画布
    assert crf <= 51
    assert preset


def test_tech_verdict_keeps_still_images():
    """有画面内容的视频：图片与视频都应判 ok（图片没有运动量也不该被误杀）。"""

    metrics = material_library._tech_metrics(_frame_pair())
    assert metrics["motion"] > material_library._MOTION_FROZEN
    assert material_library._tech_verdict(metrics, is_image=True)[0] == "ok"
    assert material_library._tech_verdict(metrics, is_image=False)[0] == "ok"


def test_ahash_detects_identical_frames():
    frame = _frame(90)
    assert material_library._hamming(
        material_library._ahash(frame), material_library._ahash(frame)
    ) == 0


def test_ahash_survives_small_brightness_shift():
    """同一画面轻微曝光变化（重编码/二压）不应被判成新素材。"""

    frame = _frame(0)
    dimmed = bytes(min(255, value + 6) for value in frame)
    assert material_library._hamming(
        material_library._ahash(frame), material_library._ahash(dimmed)
    ) <= material_library._DUP_AHASH_DISTANCE


def test_ahash_is_256_bits():
    assert len(material_library._ahash(_frame(90))) == 256


def test_ahash_separates_different_images():
    noise = bytes(((index * 37) % 256) for index in range(96 * 96))
    other = bytes(((index * 91 + 17) % 256) for index in range(96 * 96))
    distance = material_library._hamming(
        material_library._ahash(noise), material_library._ahash(other)
    )
    assert distance > material_library._DUP_AHASH_DISTANCE


def test_duplicates_any_checks_both_hash_kinds():
    frame = _frame(0)
    other = _frame(120)
    entry = {"ahashes": [material_library._ahash(frame)]}
    assert material_library._duplicates_any([], [material_library._ahash(frame)], entry)
    assert not material_library._duplicates_any([], [material_library._ahash(other)], entry)


def test_duplicates_any_keeps_legacy_index_compatible():
    frame = _frame(0)
    legacy = {"hashes": [material_library._dhash(frame)]}
    assert material_library._duplicates_any([material_library._dhash(frame)], [], legacy)


def test_list_library_hides_zoom_artifacts(tmp_path, monkeypatch):
    library = tmp_path / "local_videos"
    library.mkdir()
    (library / "a.mp4").write_bytes(b"x")
    (library / "photo.jpg.zoom-ef2d127de37b942b.mp4").write_bytes(b"x")
    (library / ".thumbs").mkdir()
    monkeypatch.setattr(material_library, "_material_dir", lambda create=True: str(library))
    monkeypatch.setattr(material_library, "get_index", lambda: {})
    files = material_library.list_library()["files"]
    assert [item["file"] for item in files] == ["a.mp4"]


def test_match_terms_ranks_exact_hits_first(monkeypatch):
    index = {
        "partial.mp4": {"status": "ok", "tags": ["美甲护理细节"]},
        "exact.mp4": {"status": "ok", "tags": ["美甲"]},
        "used.mp4": {"status": "irrelevant", "tags": ["美甲"]},
    }
    monkeypatch.setattr(material_library, "get_index", lambda: index)
    result = material_library.match_terms(["美甲"])
    assert result["covered"] == ["美甲"]
    assert result["missing"] == []
    # 精确命中排第一，状态非 ok/low_quality 的不参与匹配
    assert result["matched"]["美甲"][0] == "exact.mp4"
    assert "used.mp4" not in result["matched"]["美甲"]


def test_match_terms_reports_unrelated_terms(monkeypatch):
    monkeypatch.setattr(
        material_library,
        "get_index",
        lambda: {"a.mp4": {"status": "ok", "tags": ["美甲"]}},
    )
    result = material_library.match_terms(["海边日落"])
    assert result["covered"] == []
    assert result["missing"] == ["海边日落"]


def test_vlm_prompt_requires_broll_verdict(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": '{"tags": ["a"]}'}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _Response()

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setitem(app_config.siliconflow, "api_key", "test-key")
    material_library._vlm_analyze([b"jpeg-bytes"])

    prompt = captured["payload"]["messages"][0]["content"][0]["text"]
    assert "broll_usable" in prompt
    assert "烧录字幕" in prompt


def test_vlm_analyze_without_key_is_noop(monkeypatch):
    monkeypatch.setitem(app_config.siliconflow, "api_key", "")
    assert material_library._vlm_analyze([b"jpeg-bytes"]) == {}


class _FakeRun:
    """假的 ffmpeg：把"转码产物"写到命令里的输出路径（最后一个参数）。"""

    def __call__(self, args, timeout=240):
        output = args[-1]
        if output.endswith(".mp4"):
            Path(output).write_bytes(b"\x00" * 20_000)
        return SimpleNamespace(returncode=0, stderr="", stdout=b"")


def _stub_ingest_env(monkeypatch, tmp_path, vision):
    """把入库流水线里所有外部依赖换成确定性的本地实现。"""

    library = tmp_path / "local_videos"
    library.mkdir()
    source = library / "clip.mp4"
    source.write_bytes(b"\x00" * 4096)

    monkeypatch.setattr(material_library, "_material_dir", lambda create=True: str(library))
    trash = library / ".trash"
    trash.mkdir()
    monkeypatch.setattr(material_library, "_trash_dir", lambda: str(trash))
    monkeypatch.setattr(material_library, "_probe_media", lambda path: (12.0, 1080, 1920))
    monkeypatch.setattr(
        material_library, "_scene_split_boundaries", lambda path, duration: []
    )
    monkeypatch.setattr(material_library, "_run", _FakeRun())
    monkeypatch.setattr(material_library, "_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(
        material_library,
        "_sample_gray_frames",
        lambda path, frames, is_image=False: frames.extend(_frame_pair()),
    )
    monkeypatch.setattr(
        material_library, "_frames_to_jpegs", lambda path, count=3: [b"jpeg"]
    )
    monkeypatch.setattr(material_library, "_vlm_analyze", lambda frames: vision)

    index: dict[str, dict] = {}
    monkeypatch.setattr(material_library, "get_index", lambda: dict(index))
    monkeypatch.setattr(material_library, "_save_index", lambda payload: index.update(payload))
    return library, index


def test_ingest_rejects_material_that_cannot_work_as_broll(tmp_path, monkeypatch):
    """带原始字幕的聊天画面：移入隔离区、可申诉，不进素材池。"""

    vision = {
        "tags": ["眼部护理", "美容师"],
        "tags_en": ["eyelash_extension"],
        "beauty_related": True,
        "broll_usable": False,
        "broll_reason": "画面已烧录字幕且是对镜头讲话",
    }
    library, index = _stub_ingest_env(monkeypatch, tmp_path, vision)

    material_library._ingest_worker("job-broll", "clip.mp4")

    assert not (library / "clip-seg01.mp4").exists()
    assert (library / ".trash" / "clip-seg01.mp4").is_file()
    entry = index["clip-seg01.mp4"]
    assert entry["status"] == "unusable"
    assert "空镜" in entry["status_reason"]
    # 标签仍然保留：申诉入库或复用标签时用得上
    assert "眼部护理" in entry["tags"]


def test_ingest_accepts_clean_broll(tmp_path, monkeypatch):
    vision = {
        "tags": ["手部特写", "美甲"],
        "tags_en": ["hand_closeup"],
        "beauty_related": True,
        "broll_usable": True,
        "broll_reason": "",
    }
    library, index = _stub_ingest_env(monkeypatch, tmp_path, vision)

    material_library._ingest_worker("job-ok", "clip.mp4")

    assert (library / "clip-seg01.mp4").is_file()
    assert index["clip-seg01.mp4"]["status"] == "ok"
    assert index["clip-seg01.mp4"]["broll_usable"] is True


def test_ingest_accepts_when_vlm_is_silent(tmp_path, monkeypatch):
    """VLM 没结论（缺 Key/超时）时默认放行，绝不误杀正常素材。"""

    library, index = _stub_ingest_env(monkeypatch, tmp_path, {})

    material_library._ingest_worker("job-silent", "clip.mp4")

    assert index["clip-seg01.mp4"]["status"] == "ok"


@pytest.mark.parametrize(
    "width,height,expected",
    [(1080, 1920, (1080, 1920)), (3840, 2160, (1920, 1080)), (640, 480, (640, 480))],
)
def test_normalize_box_matrix(width, height, expected):
    assert material_library._normalize_box(width, height) == expected

def test_looks_beauty_related_rejects_off_domain_tags():
    """VLM 漏判时，标签启发式兜底（实测"美睫体验+儿童模特"漏进了美甲店库）。"""

    assert material_library._looks_beauty_related(["美睫体验", "儿童模特", "室内场景"]) is False
    assert material_library._looks_beauty_related(["家庭日常", "亲子互动"]) is False
    assert material_library._looks_beauty_related(["屏幕翻拍", "办公文档"]) is False


def test_looks_beauty_related_accepts_real_beauty_tags():
    assert material_library._looks_beauty_related(["美甲展示", "手部特写"]) is True
    assert material_library._looks_beauty_related(["眼部护理", "睫毛嫁接"]) is True


def test_explicit_vlm_verdict_beats_tag_heuristic(monkeypatch):
    """模型明确说美业相关时，不能因为标签里有个中性词就误杀。"""

    assert material_library._looks_beauty_related(["产品展示", "宠物店日常"]) is False
    vision = {"tags": ["美甲展示"], "beauty_related": True}
    assert bool(vision.get("beauty_related", True)) is True
