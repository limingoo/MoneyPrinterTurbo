"""Create a video task with an uploaded custom voiceover audio file.

The stock ``POST /api/v1/videos`` accepts ``custom_audio_file`` only when the
file already exists inside the task directory, and there is no HTTP channel to
place one there (the Streamlit WebUI stages uploads in-process). This module
adds a single multipart endpoint that closes that gap for HTTP clients:

1. parse the task params from a JSON form field,
2. create the task directory and store the uploaded audio inside it,
3. dispatch the task only after the file is safely in place.

Everything lives in this new module; the only upstream touch is a two-line
router registration in ``app/router.py``.
"""

import os
import shutil

from fastapi import Depends, File, Form, Request, UploadFile
from loguru import logger

from app.controllers import base
from app.controllers.manager.base_manager import TaskQueueFullError
from app.controllers.v1.base import new_router
from app.controllers.v1.video import task_manager
from app.models.exception import HttpException
from app.models.schema import TaskVideoRequest
from app.services import bgm as bgm_service
from app.services import state as sm
from app.services import task as tm
from app.utils import file_security, utils

# 与 BGM 上传共用扩展名白名单和大小上限，保持全仓库一致的音频准入规则。
_MAX_AUDIO_BYTES = bgm_service.MAX_BGM_UPLOAD_BYTES
_FIXED_STORED_NAME_PREFIX = "custom_audio"

router = new_router(dependencies=[Depends(base.verify_token)])


def _audio_extension(filename: str | None, request_id: str) -> str:
    """提取并校验音频扩展名，返回带点的标准化后缀。"""

    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    extension = os.path.splitext(name)[1].lower()
    if extension not in bgm_service.SUPPORTED_BGM_EXTENSIONS:
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=f"{request_id}: unsupported audio format",
        )
    return extension


def _save_audio_into_task_dir(
    task_id: str, upload: UploadFile, request_id: str
) -> str:
    """把上传音频写进任务目录，返回固定重命名后的相对文件名。

    存储名完全不使用客户端文件名，杜绝路径拼接与同名覆盖两类风险；
    扩展名经过白名单校验，ffmpeg 探测失败时立即清理并报错。
    """

    extension = _audio_extension(upload.filename, request_id)
    stored_name = f"{_FIXED_STORED_NAME_PREFIX}{extension}"
    task_dir = utils.task_dir(task_id)
    target_path = os.path.join(task_dir, stored_name)

    copied = 0
    try:
        with open(target_path, "wb") as output:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > _MAX_AUDIO_BYTES:
                    raise HttpException(
                        task_id=request_id,
                        status_code=400,
                        message=(
                            f"{request_id}: audio file exceeds "
                            f"{_MAX_AUDIO_BYTES // (1024 * 1024)} MB limit"
                        ),
                    )
                output.write(chunk)
    except HttpException:
        _cleanup_file(target_path)
        raise
    except OSError:
        _cleanup_file(target_path)
        logger.error(
            f"custom audio upload failed: request_id={request_id}, task_id={task_id}"
        )
        raise HttpException(
            task_id=request_id,
            status_code=500,
            message=f"{request_id}: audio storage is unavailable",
        )

    if copied == 0:
        _cleanup_file(target_path)
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=f"{request_id}: uploaded audio file is empty",
        )

    # ffmpeg 探测保证是真实可解码的音频，坏文件在上传阶段就被拒绝，
    # 不至于让任务跑到配音阶段才失败。
    from app.services import voice as voice_service

    try:
        duration = voice_service.get_audio_duration(target_path)
    except Exception:  # noqa: BLE001 - 探测层任何异常都按无效音频处理
        duration = 0
    if not duration or duration <= 0:
        _cleanup_file(target_path)
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=f"{request_id}: uploaded file is not decodable audio",
        )

    return stored_name


def _cleanup_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


@router.post(
    "/videos/custom_audio",
    summary="Create a short video with an uploaded custom voiceover",
    description=(
        "Multipart request: `params` holds the VideoParams JSON, `file` is the "
        "voiceover audio. The audio is stored inside the task directory before "
        "the task is queued, so it reliably replaces TTS in the pipeline."
    ),
)
def create_video_with_custom_audio(
    request: Request,
    params: str = Form(...),
    file: UploadFile = File(...),
):
    request_id = base.get_task_id(request)
    task_id = utils.get_uuid()

    try:
        body = TaskVideoRequest.model_validate_json(params)
    except Exception as exc:  # noqa: BLE001 - 统一转成 400，错误细节留在 message
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=f"{request_id}: invalid video params ({exc})",
        )

    # 客户端传入的 custom_audio_file 一律忽略，以服务端实际存储为准，
    # 防止把任意路径注入任务流水线。
    body.custom_audio_file = _save_audio_into_task_dir(task_id, file, request_id)

    try:
        if body.subtitle_enabled:
            tm.video.validate_subtitle_colors(body)
            file_security.resolve_path_within_directory(
                utils.font_dir(), body.font_name or "STHeitiMedium.ttc"
            )
        task = {
            "task_id": task_id,
            "request_id": request_id,
            "params": body.model_dump(),
        }
        # Same reasoning as create_video: persist the subject so the task list
        # can label the task until the background storyboard sets `task_name`.
        sm.state.update_task(task_id, video_subject=body.video_subject or "")
        try:
            task_manager.add_task(tm.start, task_id=task_id, params=body, stop_at="video")
        except Exception:
            sm.state.delete_task(task_id)
            raise
        return utils.get_response(200, task)
    except TaskQueueFullError as exc:
        _cleanup_task_dir(task_id)
        logger.warning(f"reject custom audio task: {exc}")
        raise HttpException(
            task_id=task_id, status_code=429, message=f"{request_id}: {exc}"
        )
    except ValueError as exc:
        _cleanup_task_dir(task_id)
        raise HttpException(
            task_id=task_id, status_code=400, message=f"{request_id}: {exc}"
        )
    except HttpException:
        raise
    except Exception:
        _cleanup_task_dir(task_id)
        raise


def _cleanup_task_dir(task_id: str) -> None:
    """调度失败时清掉刚创建的任务目录，避免残留孤儿音频文件。"""

    task_dir = utils.task_dir(task_id)
    shutil.rmtree(task_dir, ignore_errors=True)
