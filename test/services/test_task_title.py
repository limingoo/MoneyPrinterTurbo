from unittest.mock import patch

from app.services import llm, task as tm
from app.services.state import MemoryState


def test_task_title_uses_the_generated_title():
    with patch.object(llm, "_generate_response", return_value='"复利到底怎么滚"') as response:
        assert llm.generate_task_title("复利", "讲清楚复利的原理") == "复利到底怎么滚"
    assert response.call_count == 1


def test_task_title_is_clamped_and_stripped_of_wrappers():
    raw = f'{llm.MAX_TASK_TITLE_LENGTH + 10}字标题'
    with patch.object(llm, "_generate_response", return_value=f'1. **标题：{raw}。**'):
        actual = llm.generate_task_title("复利", "")
    assert actual == raw[: llm.MAX_TASK_TITLE_LENGTH]


def test_task_title_accepts_a_json_object():
    with patch.object(llm, "_generate_response", return_value='{"title":"复利的真正威力"}'):
        assert llm.generate_task_title("复利", "") == "复利的真正威力"


def test_task_title_provider_error_falls_back_to_the_subject():
    with patch.object(llm, "_generate_response", return_value="Error: provider unavailable") as response:
        actual = llm.generate_task_title("请帮我做一个关于复利的短视频。", "")
    assert actual == "做一个关于复利的短视频"
    # 标题是展示字段，provider 报错时不应该反复重试拖慢流水线。
    assert response.call_count == 1


def test_task_title_stays_empty_without_any_usable_text():
    with patch.object(llm, "_generate_response", return_value="   "):
        assert llm.generate_task_title("", "") == ""


def test_task_title_falls_back_to_the_script_when_the_subject_is_empty():
    with patch.object(llm, "_generate_response", return_value="Error: provider unavailable"):
        actual = llm.generate_task_title("", "第一句话。第二句话。")
    assert actual == "第一句话"

def test_naming_a_task_keeps_the_progress_already_reached():
    """任务名是补丁，不能像 update_task 那样把进度清零。

    update_task 的 state/progress 有默认值，只传 task_name 会把脚本阶段刚写入的
    progress 退回 0，任务列表会看到进度条回退。
    """
    state = MemoryState()
    state.update_task("task-1", progress=5)
    with patch.object(tm.sm, "state", state), patch.object(
        llm, "generate_task_title", return_value="复利到底怎么滚"
    ):
        tm._name_task("task-1", type("P", (), {"video_subject": "复利"})(), "脚本")
    task = state.get_task("task-1")
    assert task["task_name"] == "复利到底怎么滚"
    assert task["progress"] == 5


def test_naming_a_task_never_resurrects_a_deleted_task():
    state = MemoryState()
    state.update_task("task-1", progress=5)
    state.delete_task("task-1")
    with patch.object(tm.sm, "state", state), patch.object(
        llm, "generate_task_title", return_value="复利到底怎么滚"
    ):
        tm._name_task("task-1", type("P", (), {"video_subject": "复利"})(), "脚本")
    assert state.get_task("task-1") is None


def test_naming_a_task_survives_a_title_failure():
    state = MemoryState()
    state.update_task("task-1", progress=5)
    with patch.object(tm.sm, "state", state), patch.object(
        llm, "generate_task_title", side_effect=RuntimeError("gateway stalled")
    ):
        tm._name_task("task-1", type("P", (), {"video_subject": "复利"})(), "脚本")
    task = state.get_task("task-1")
    assert "task_name" not in task
    assert task["progress"] == 5
