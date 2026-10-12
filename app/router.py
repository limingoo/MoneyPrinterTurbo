"""Application configuration - root APIRouter

Defines all FastAPI application endpoints.

Resources:
    1. https://fastapi.tiangolo.com/tutorial/bigger-applications

"""

from fastapi import APIRouter

from app.controllers import ping
from app.controllers.v1 import (
    config_api,
    custom_audio,
    llm,
    material_library,
    storyboard,
    video,
    voice_clone,
)

root_api_router = APIRouter()
root_api_router.include_router(ping.router)

# v1
root_api_router.include_router(video.router)
root_api_router.include_router(llm.router)
# 新增的独立模块：带自定义配音上传的视频任务创建（前端的配套接口）
root_api_router.include_router(custom_audio.router)
# 新增的独立模块：运行期配置的读写（前端的可视化设置页配套接口）
root_api_router.include_router(config_api.router)
# 新增的独立模块：声音克隆（SiliconFlow 音色复刻）
root_api_router.include_router(voice_clone.router)
# 新增的独立模块：素材库（智能入库/打标/预检/变体）
root_api_router.include_router(material_library.router)
# 新增的独立模块：分镜拆分（文案 → 分镜表）
root_api_router.include_router(storyboard.router)
