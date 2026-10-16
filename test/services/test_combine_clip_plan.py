"""combine_videos 的分镜计划分支：顺序、目标时长与兜底。

用真实 ffmpeg 跑三段纯色素材，直接从成片里读像素确认"旁白念到哪、画面切到哪"。
"""

import subprocess
import wave

import numpy as np
import pytest

from app.models.schema import VideoAspect, VideoConcatMode
from app.services import video

FRAME_RATE = 10


def _make_source(binary, path, color, seconds):
    subprocess.run(
        [
            binary, "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r={FRAME_RATE}",
            "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return str(path)


def _make_audio(path, seconds):
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b"\0\0" * int(24000 * seconds))
    return str(path)


def _channel_at(path, second):
    """按最大通道判定颜色：0=red, 1=green, 2=blue。"""

    with video.VideoFileClip(path, audio=False) as clip:
        frame = clip.get_frame(min(second, clip.duration - 0.05))
    return int(np.argmax(np.mean(frame, axis=(0, 1))))


@pytest.fixture
def combine_env(tmp_path, monkeypatch):
    binary = video.utils.get_ffmpeg_binary()
    monkeypatch.setattr(VideoAspect, "to_resolution", lambda self: (64, 64))
    monkeypatch.setattr(video, "_get_configured_video_codec", lambda: "libx264")
    monkeypatch.setattr(video, "_get_effective_video_codec", lambda *args: "libx264")
    monkeypatch.setattr(video, "_SCENE_ALIGN_ENABLED", False)
    return binary


def test_clip_plan_drives_order_and_durations(combine_env, tmp_path):
    binary = combine_env
    red = _make_source(binary, tmp_path / "red.mp4", "red", 10)
    blue = _make_source(binary, tmp_path / "blue.mp4", "blue", 10)
    green = _make_source(binary, tmp_path / "green.mp4", "green", 10)
    audio = _make_audio(tmp_path / "audio.wav", 6.0)
    output = tmp_path / "combined.mp4"

    # 计划与传入顺序故意相反：顺序必须由计划决定
    video.combine_videos(
        str(output),
        [green, blue, red],
        audio,
        video_concat_mode=VideoConcatMode.sequential,
        threads=1,
        max_clip_duration=5,
        clip_plan=[
            {"file_path": red, "start_time": 0, "end_time": 2, "target_duration": 2.0},
            {"file_path": blue, "start_time": 0, "end_time": 2, "target_duration": 2.0},
            {"file_path": green, "start_time": 0, "end_time": 2.1, "target_duration": 2.1},
        ],
    )

    # 计划顺序与时长落在成片时间轴上：0-2s 红、2-4s 蓝、4-6s 绿
    assert _channel_at(str(output), 0.5) == 0
    assert _channel_at(str(output), 1.8) == 0
    assert _channel_at(str(output), 2.2) == 2
    assert _channel_at(str(output), 3.8) == 2
    assert _channel_at(str(output), 4.2) == 1
    assert _channel_at(str(output), 5.8) == 1
    with video.VideoFileClip(str(output), audio=False) as clip:
        assert clip.duration == pytest.approx(6.0, abs=0.3)


def test_clip_plan_reads_only_the_requested_source_range(combine_env, tmp_path):
    """同一素材切成两段时，第二段必须从计划给的 start_time 开始取画面。"""

    binary = combine_env
    red = _make_source(binary, tmp_path / "red.mp4", "red", 6)
    blue = _make_source(binary, tmp_path / "blue.mp4", "blue", 6)
    audio = _make_audio(tmp_path / "audio.wav", 4.0)
    output = tmp_path / "combined.mp4"

    video.combine_videos(
        str(output),
        [red, blue],
        audio,
        video_concat_mode=VideoConcatMode.sequential,
        threads=1,
        max_clip_duration=5,
        clip_plan=[
            {"file_path": red, "start_time": 0, "end_time": 2, "target_duration": 2.0},
            {"file_path": red, "start_time": 4, "end_time": 6, "target_duration": 2.1},
        ],
    )
    # 计划只引用 red 时，成片不该混入 blue 的画面
    assert _channel_at(str(output), 0.5) == 0
    assert _channel_at(str(output), 3.0) == 0


def test_clip_plan_target_duration_overrides_clip_cap(combine_env, tmp_path):
    binary = combine_env
    red = _make_source(binary, tmp_path / "red.mp4", "red", 12)
    audio = _make_audio(tmp_path / "audio.wav", 8.0)
    output = tmp_path / "combined.mp4"

    video.combine_videos(
        str(output),
        [red],
        audio,
        video_concat_mode=VideoConcatMode.sequential,
        threads=1,
        max_clip_duration=3,
        clip_plan=[
            {"file_path": red, "start_time": 0, "end_time": 4, "target_duration": 4.0},
            {"file_path": red, "start_time": 4, "end_time": 8.1, "target_duration": 4.1},
        ],
    )
    with video.VideoFileClip(str(output), audio=False) as clip:
        # 每段 4 秒（> max_clip_duration=3），但没被截断
        assert clip.duration == pytest.approx(8.0, abs=0.3)


def test_unusable_clip_plan_falls_back_to_sources(combine_env, tmp_path):
    """计划里全是坏条目时必须退回固定步长拼接，而不是产出一段空视频。"""

    binary = combine_env
    red = _make_source(binary, tmp_path / "red.mp4", "red", 6)
    audio = _make_audio(tmp_path / "audio.wav", 3.0)
    output = tmp_path / "combined.mp4"

    video.combine_videos(
        str(output),
        [red],
        audio,
        video_concat_mode=VideoConcatMode.sequential,
        threads=1,
        max_clip_duration=2,
        clip_plan=[
            {"file_path": str(tmp_path / "missing.mp4"), "start_time": 0, "end_time": 2, "target_duration": 2.0},
        ],
    )
    assert output.is_file()
    assert _channel_at(str(output), 0.5) == 0


def test_used_video_paths_follow_the_plan(combine_env, tmp_path):
    binary = combine_env
    red = _make_source(binary, tmp_path / "red.mp4", "red", 4)
    blue = _make_source(binary, tmp_path / "blue.mp4", "blue", 4)
    audio = _make_audio(tmp_path / "audio.wav", 4.0)
    used: list[str] = []

    video.combine_videos(
        str(tmp_path / "combined.mp4"),
        [red, blue],
        audio,
        video_concat_mode=VideoConcatMode.sequential,
        threads=1,
        max_clip_duration=2,
        used_video_paths=used,
        clip_plan=[
            {"file_path": blue, "start_time": 0, "end_time": 2, "target_duration": 2.0},
            {"file_path": red, "start_time": 0, "end_time": 2.1, "target_duration": 2.1},
        ],
    )
    assert used == [blue, red]