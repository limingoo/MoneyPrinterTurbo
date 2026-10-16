# MoneyPrinterTurbo 二次开发项目说明手册

> 本文档面向"喂给 AI 模型即可进行二次开发"的目标编写。读完本文档即可理解项目架构、核心流水线、API 契约、配置体系与扩展点。文档基于 2026-09 代码库快照。
>
> 项目根目录（下文以 `ROOT` 指代）：本文件所在仓库根目录。

---

## 1. 项目概览

**MoneyPrinterTurbo**：一站式 AI 短视频生成工具。输入一个视频**主题**或**关键词**，自动完成：LLM 生成脚本 → 提取关键词 → TTS 配音 → 字幕生成 → 素材获取（免费库存 / AI 文生视频 / 本地上传）→ MoviePy 合成 → （可选）一键跨平台发布到 TikTok / Instagram / YouTube Shorts。

- 语言：Python 3.11
- 后端：FastAPI + uvicorn（API 服务，端口 8080）
- 前端：Streamlit（WebUI，端口 8501）
- CLI：`cli.py`（批量 JSON/JSONL 任务清单）
- 本仓库为上游 `harry0703/MoneyPrinterTurbo` 的二次开发版，本地新增功能包括：MuAPI 文生视频、秘塔 MiniMax H3 文生视频、OFox 文生视频、胜算云 LoomLoom 批量素材、Sonilo / ElevenLabs AI 配乐、素材上传 API、任务产物持久化加强等。

---

## 2. 目录结构

```
ROOT/
├── main.py                    # API 启动入口：uvicorn app.asgi:app
├── cli.py                     # CLI 入口（约 1791 行）：批量任务清单驱动
├── app/                       # FastAPI 后端
│   ├── asgi.py                # FastAPI 应用对象（CORS、/tasks 静态文件）
│   ├── router.py              # 根路由注册：/ping + /api/v1/{video,llm}
│   ├── config/config.py       # config.toml 加载 + 线程安全运行时配置（约 592 行）
│   ├── controllers/
│   │   ├── base.py            # x-api-key 鉴权 verify_token、x-task-id 日志追踪
│   │   ├── ping.py            # GET /ping
│   │   ├── manager/           # 任务队列：InMemoryTaskManager / RedisTaskManager
│   │   └── v1/                # video.py、llm.py（前缀 /api/v1）
│   ├── models/
│   │   ├── schema.py          # 全部 Pydantic 请求/响应模型（VideoParams 等）
│   │   ├── llm_provider.py    # LLM Provider 注册表（28 个 provider 声明）
│   │   ├── const.py           # 常量：任务状态码、文件类型等
│   │   └── exception.py
│   ├── services/              # 业务实现（见 §5.2 服务清单）
│   └── utils/utils.py         # 任务目录管理、ffmpeg 检测等
├── webui/
│   ├── Main.py                # Streamlit 主界面（约 8262 行）
│   ├── styles.css、i18n/、.streamlit/config.toml
├── docs/skill/                # Agent Skill（SKILL.md + mpt_agent.py helper）
├── test/                      # pytest 测试（60+ 文件）
├── resource/                  # songs（内置 BGM）、fonts（字幕字体）
├── storage/                   # 运行产物：tasks/<task_id>/、bgm、local_videos、cache_videos、uploads
├── config.toml / config.example.toml
├── Dockerfile（+ .gpu / .claude 变体）
├── docker-compose.yml（+ .release / .gpu / .claude 变体）
├── requirements.txt / pyproject.toml / uv.lock
└── webui.sh / webui.bat       # Streamlit 本地启动脚本
```

---

## 3. 快速运行

### Docker（推荐）
```bash
docker compose up -d          # 构建 + 启动 webui(8501) 与 api(8080)
# 国内网络首次构建需加： --build-arg DOCKER_BUILD_MIRROR=china
```
- `webui`：Streamlit，`127.0.0.1:8501`
- `api`：uvicorn，`127.0.0.1:8080`，API 文档 `http://127.0.0.1:8080/docs`
- 两个服务把 `./` 挂载到容器 `/MoneyPrinterTurbo`，产物直接写宿主机 `storage/`
- 镜像内 `FROM python:3.11-slim-bullseye`；本仓库 Dockerfile 已内置中国镜像回退链（阿里云→清华→官方）

### 本地直接运行
```bash
pip install -r requirements.txt      # 或 uv sync
python3 main.py                      # API 服务
./webui.sh                           # WebUI（Windows 用 webui.bat）
```

### 测试
```bash
uv run python -X utf8 -m pytest -q test                        # 全量
uv run python -X utf8 -m pytest -q test/services/test_video.py # 单文件
```

---

## 4. 配置体系（app/config/config.py）

### 加载规则
- 模块级执行 `load_config()`；`config.toml` 不存在则从 `config.example.toml` 复制。
- TOML 解析兼容 UTF-8 BOM。
- 顶层键直接暴露：`log_level`、`listen_host`、`listen_port (8080)`。
- 分区对象：`app`（约 150 键，承载几乎所有业务配置与 API Key）、`whisper`、`proxy`、`azure`、`siliconflow`、`minimax_tts`、`elevenlabs`、`chatterbox`、`kokoro`、`fish_audio`、`voxcpm`、`ui`。
- 环境变量：Redis 主机优先读 `MPT_APP_REDIS_HOST` / `REDIS_HOST`；Ollama 在容器内自动解析 `host.docker.internal` 为 base_url。

### config.toml 主要分区
| 分区 | 关键内容 |
|---|---|
| 顶层 | log_level、listen_host、listen_port |
| `[app]` | api_key（非空则 API 强制鉴权）；素材 Key（pexels_api_keys/pixabay_api_keys/coverr/wavespeed）；volcengine_seedance_*；ofox_*；muapi_*；metaso_minimax_*；openai_image_*；twelvelabs_*；loomloom_*；sonilo_*；每个 LLM provider 的 `<id>_api_key / <id>_base_url / <id>_model_name`；mimo_tts_*；subtitle_provider="edge"；endpoint；enable_redis、redis_*、max_concurrent_tasks(5)、max_queued_tasks(100)；upload_post_* |
| `[whisper]` | model_size=large-v3、device=cpu、compute_type=int8 |
| `[ui]` | WebUI 保存的生成默认值（语言/画幅/音色/字幕样式等）；CLI 参数优先级：命令行 > [ui] > 内置默认 |
| 其他 TTS 分区 | azure、siliconflow、minimax_tts、elevenlabs（含 music_* 配乐参数）、chatterbox(localhost:4123)、kokoro(localhost:8880)、fish_audio、voxcpm(api.modelbest.cn) |

### 运行时并发安全（重要）
- `_SynchronizedConfig` dict + `_config_save_lock`。
- WebUI 写配置走非阻塞 API：`update_config_nonblocking / try_save_config`（pending 队列 + 后台 flush 线程）；读合并快照用 `snapshot_config_with_pending()`。
- 任务执行期间用 `runtime_config_lock()` 防止中途切换 Provider。
- **二次开发须知：任何配置写入都必须走 config 模块的非阻塞 API，禁止直接改 `config.app[...]` 后立刻存盘，否则会与长任务锁互卡。**

---

## 5. 架构与核心流水线

### 5.1 请求入口
路由（`app/router.py`）：`/ping`（无鉴权）+ `/api/v1/{video, llm}`（统一带 `Depends(base.verify_token)`）。

鉴权（`app/controllers/base.py`）：`config.app["api_key"]` 非空时强制校验 `x-api-key` 头（恒定时间比较、拒绝重复 Header）；为空则免鉴权。任务追踪用 `x-task-id` 头（非法则生成 UUID）。

### 5.2 app/services 服务清单

| 文件 | 行数 | 职责 |
|---|---|---|
| `task.py` | ~1750 | **核心编排**：`start()` → `_run_pipeline()`，全流程与进度刻度 |
| `material.py` | ~2511 | 素材统一下载入口 `download_videos()`：Pexels/Pixabay/Coverr/WaveSpeed/OpenAI 文生图；按脚本顺序匹配 |
| `video.py` | ~1650 | MoviePy：`combine_videos()` 素材拼接、`generate_video()` 最终合成（字幕/字体路径校验/BGM/转场/变速） |
| `voice.py` | ~3198 | 全部 TTS 适配器 + `create_subtitle()` + `get_audio_duration()` |
| `llm.py` | ~1214 | LLM 统一调度：`generate_script / generate_terms / generate_social_metadata` |
| `subtitle.py` | ~351 | faster-whisper 字幕 + `correct()` 脚本纠错 |
| `bgm.py` | - | 本地 BGM 枚举/上传校验（30MB 上限、Win32 文件名安全） |
| `material_upload.py` | ~310 | API 本地素材上传校验/落盘（UUID 名、200MB 视频/20MB 图片、C1 控制符/双向文本/Win32 保留名拒绝） |
| `task_artifacts.py` | ~96 | 任务目录 `script.json` 原子读写（write_script_data/patch_script_data，含素材 provenance） |
| `state.py` | - | 任务状态存储（内存/Redis，Redis 用 Lua patch 保证原子性） |
| `cache_manager.py` | ~215 | `storage/cache_videos` 视频 AI 生成缓存统计/清理（`mid-<md5>.mp4`） |
| `material_cache.py` | - | 在线素材搜索结果 JSON 磁盘缓存（TTL 24h，每小时清理） |
| `muapi.py` | ~456 | MuAPI 异步文生视频：默认 `https://api.muapi.ai/api/v1`、endpoint `seedance-lite-t2v`、3–12s、480p/720p/1080p、5s 轮询、1800s 总超时 |
| `metaso_minimax.py` | ~461 | 秘塔 MiniMax H3：`https://metaso.cn/api/minimax`、model `MiniMax-H3`、768P/2K、4–15s、prompt≤7000 |
| `ofox.py` | ~474 | OFox 多模型：`https://api.ofox.ai/v1`、默认 `bytedance/seedance-2.0-fast`（4–15s）、厂商通道默认 `byteplus` |
| `loomloom.py` | ~979 | 胜算云 SkillBot 批量客户端：quote→confirm→run→下载；脚本与视频两个 Listing；需 WebUI 确认后传入 `start()` |
| `volcengine_seedance.py` | - | 火山 Seedance（`doubao-seedance-1-0-pro-250528`、2–12s、480p/720p/1080p） |
| `twelvelabs.py` | - | 可选：Marengo 语义重排搜索词、Pegasus 视频理解；无 Key 时全部 no-op |
| `sonilo.py` | ~386 | Sonilo 视频-配乐（视频≤300MB、音频≤30MB、prompt≤2000），输出 .m4a |
| `elevenlabs_music.py` | ~412 | ElevenLabs video-to-music（music_v2、视频≤600s、prompt≤1000），复用 `[elevenlabs] api_key`，输出 .mp3 |
| `upload_post.py` | - | upload-post.com 跨平台发布 |
| `webui_task.py` / `version_checker.py` | - | WebUI 专用任务封装（并发=1）/ GitHub 版本检查 |

**新增服务的统一模式**：`is_enabled()` 检查 + 异常类携带远端 task_id（供恢复）+ 提交/轮询/下载 + `MAX_PROMPT_LENGTH`、`validate_generation_access()` 校验。提交后疑似模糊响应**绝不重试**（任务已计费）。

### 5.3 视频生成流水线（task.py::_run_pipeline）

进度刻度：`5 preflight → 10 script → 20 terms → 30 audio → 40 subtitle → 50 素材 → 合成期 50→100`。

1. **preflight**（进度 5）：各素材/配乐源 `is_enabled()` 检查；volcengine/ofox/metaso/muapi/openai_image/sonilo/elevenlabs 预检（含 prompt 长度校验）；stop_at 非 script/terms 时执行 `check_ffmpeg_ready()`。
2. **generate_script**（5→10）：`video_script` 非空直接用，否则 `llm.generate_script()`；失败标记 `failed_stage="script"`。
3. **generate_terms**（10→20）：非 local 源时 `llm.generate_terms()`（顺序匹配时 amount=8 并按脚本顺序；否则 5，可选 twelvelabs 重排）；写 `script.json`（原子写）。`stop_at` 可在 script/terms/audio/subtitle/materials 各阶段提前终止并返回 100%。
4. **generate_audio**（20→30）：优先级 `custom_audio_file`（任务目录内白名单解析）→ WebUI 试听缓存（重校验参数一致性）→ `voice.tts()` 生成 `audio.mp3`。实测时长（含 TTS 尾部）作为 audio_duration。
5. **generate_subtitle**（30→40）：`edge`（默认）用 TTS 时间轴 `create_subtitle()`；`whisper` 用 faster-whisper 转写 + `subtitle.correct()` 纠错。edge 失败**不会**自动降级 whisper（避免隐式下载数 GB 模型）。
6. **get_video_materials**（40→50）：
   - `local` → `video.preprocess_video()`；
   - `loomloom` → 执行已确认的付费 run 并持久化 run_id；
   - 其他 → `material.download_videos()`（顺序匹配时长预算 = 音频时长 × video_count）；AI 视频源异常统一携带远端 task_id。
7. **generate_final_videos**（50→100）：循环 `video_count` 次：`combine_videos()` → 可选 AI BGM（`bgm_type in {sonilo, elevenlabs}`，失败降级无 BGM 并加 warnings）→ `generate_video()` 输出 `final-N.mp4`。批量任务在 stock/local 源下做素材去重分配，写入 `material_selections`。
8. **跨平台发布**：COMPLETE 后若 `upload_post.auto_upload`，线程池异步执行：先 `generate_social_metadata`（YouTube 用 title/description/tags + 隐私状态），再逐视频发布；启动时 `recover_interrupted_cross_posts()` 回收中断任务。

### 5.4 合成阶段参数（video.py）

- `combine_videos(combined_video_path, video_paths, audio_file, video_aspect, video_concat_mode, video_transition_mode, max_clip_duration, threads, clip_speed, video_fit_mode, source_usage...)`：目标时长 = 音频时长 + 安全余量；`source_clip_duration = max_clip_duration * clip_speed` 反推源片段长度（保证变速后源时间线连续）；FFmpeg concat 优先，编解码失败自动回退 MoviePy。
- `generate_video(video_path, audio_path, subtitle_path, output_file, params, bgm_file_override)`：分辨率由 Aspect 决定；渲染字幕（字体/颜色/描边/圆角背景/pop_spring 弹簧动画）；`bgm_file_override` 优先级高于 bgm_type 解析（None=走 bgm_service，""=禁用）；返回 bool 表示 BGM 混音成功与否（失败仍输出无 BGM 成片）。

### 5.5 VideoParams 关键字段（app/models/schema.py）

```
video_subject(必填)          # 主题
video_script=""              # 直接提供脚本则跳过 LLM
video_terms=None             # 直接提供关键词则跳过 LLM
video_aspect="9:16"          # 9:16 | 16:9 | 1:1
video_fit_mode="cover"       # cover | contain
video_concat_mode="random"   # random | sequential
video_transition_mode=None   # Shuffle|FadeIn|FadeOut|SlideIn|SlideOut|ZoomIn|ZoomOut
video_clip_duration=5        # ≥1
video_clip_speed=1.0         # 变速
match_materials_to_script=False
video_count=1
video_source="pexels"        # 见 §7.3
video_materials=[MaterialInfo{provider,url,duration,source_info}]
custom_audio_file=None、voice_name=""、voice_volume=1.0、voice_rate=1.0
bgm_type="random"、bgm_file=""、bgm_volume=0.2、video_music_prompt=""(≤2000)
subtitle_enabled=True        # bool（919170b 起严格 bool 解析）；position: top/bottom/center/custom/two_thirds_bottom
subtitle_display_mode="sentence" | "word_by_word"
subtitle_animation="none" | "pop_spring"
custom_position=70.0、font_name="STHeitiMedium.ttc"、text_fore_color="#FFFFFF"
text_background_color=False|色值、rounded_subtitle_background=False
font_size=60、stroke_color="#000000"、stroke_width=1.5
n_threads=2、paragraph_number=1(1-10)
video_script_prompt(≤2000)、custom_system_prompt(≤8000)
```

---

## 6. API 完整契约

所有响应统一 `{status, message, data}`（BaseResponse）。创建任务失败（队列满）返回 429。

### 6.1 video 控制器（app/controllers/v1/video.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/videos` | 生成完整视频。body = `TaskVideoRequest`（即 VideoParams）→ `{data:{task_id}}` |
| POST | `/api/v1/audio` | 只生成到配音，`stop_at="audio"` |
| POST | `/api/v1/subtitle` | 只生成到字幕，`stop_at="subtitle"` |
| GET | `/api/v1/tasks?page=&page_size=` | 分页任务列表 `TaskListResponse` |
| GET | `/api/v1/tasks/{task_id}` | 任务状态（state 1=完成/-1=失败、progress、videos、combined_videos、failed_stage、error、cross_post_*）；内部字段 `cross_post_owner` 被剔除 |
| DELETE | `/api/v1/tasks/{task_id}` | 删除任务；运行中返回 409 |
| GET | `/api/v1/musics` | 列出本地 BGM（storage/bgm + resource/songs 白名单，只返回文件名） |
| POST | `/api/v1/musics` | 上传 BGM（mp3/m4a/aac/wav/flac/ogg/opus/wma，≤30MB） |
| GET | `/api/v1/video_materials` | 列出本地素材（storage/local_videos） |
| POST | `/api/v1/video_materials` | 上传本地素材（视频 6 格式 ≤200MB / 图片 jpg,jpeg,png,bmp ≤20MB） |
| GET | `/api/v1/stream/{file_path:path}` | 视频 Range 流式播放（206） |
| GET | `/api/v1/download/{file_path:path}` | 文件下载（防路径穿越 403/404） |

### 6.2 llm 控制器（app/controllers/v1/llm.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/scripts` | `{video_subject, video_language, paragraph_number, video_script_prompt≤2000, custom_system_prompt≤8000}` → `{video_script}` |
| POST | `/api/v1/terms` | `{video_subject, video_script, amount, match_materials_to_script}` → `{video_terms:[…]}` |
| POST | `/api/v1/social-metadata` | `{video_subject, video_script, language, platform}` → `{title, caption, hashtags}` |

### 6.3 请求示例

```bash
curl -X POST "http://127.0.0.1:8080/api/v1/videos" \
  -H "Content-Type: application/json" -H "x-api-key: <你的key，若已配置>" \
  -d '{
    "video_subject": "A day in Shanghai",
    "video_terms": ["sky", "tree"],
    "video_aspect": "16:9",
    "video_clip_duration": 5,
    "video_count": 1,
    "voice_name": "zh-CN-XiaoxiaoNeural-Female",
    "bgm_type": "random",
    "bgm_volume": 0.2,
    "subtitle_enabled": true,
    "subtitle_position": "bottom",
    "font_size": 60,
    "n_threads": 2,
    "paragraph_number": 1
  }'
```
响应：`{"status":200,"message":"success","data":{"task_id":"6c85c8cc-…"}}`
随后轮询 `GET /api/v1/tasks/{task_id}`（带 `x-task-id` 日志头）直到 state=1。

---

## 7. Provider 支持矩阵

### 7.1 LLM（app/models/llm_provider.py 注册表，默认 provider=moonshot）

- **直连**：moonshot（默认模型 kimi-k3，中国/Global 双端点）、openai(gpt-5.5)、anthropic(claude-sonnet-5)、gemini、deepseek、qwen、azure、volcengine、grok、minimax、mimo
- **聚合网关**：shengsuanyun(胜算云)、apimart、cloudflare(需 account_id/gateway_id)、modelscope、aihubmix、aimlapi、evolink、openrouter、api_route、fluxionai、cheaperinference(Cheaper Inference，OpenAI 兼容网关，默认模型 gpt-5.4-mini)、groq(默认模型已从退役的 llama-3.3-70b-versatile 换成 openai/gpt-oss-120b)、pollinations
- **特殊**：ollama（无需 key）、claude_code（调本机 claude CLI，可配 cli_path/timeout）、oneapi、litellm、pollinations
- 大多数 provider 是 OpenAI 兼容协议 → 在注册表加一个 `LLMProviderSpec` 即零代码接入；仅协议不同才写 adapter（llm.py 内有 openai_compatible/gemini/qwen/azure/cloudflare_ai_gateway/modelscope/claude_code/litellm/ollama 共 9 种 adapter）。
- 非 WebUI 选中的 provider 值通过 `normalize_provider_override` 不固化为配置，避免污染 config.toml。

### 7.2 TTS（voice.py，WebUI voice_mode = none/upload/tts）

`tts_server` 可选值：`azure-tts-v1`(Edge TTS，默认)、`azure-tts-v2`、`siliconflow`、`gemini-tts`、`mimo-tts`、`minimax-tts`、`elevenlabs`、`chatterbox`、`kokoro`、`fish_audio`、`voxcpm`（音色克隆，可传参考音频 bytes ≤20MiB）。

### 7.3 素材来源（video_source，注册于 webui/Main.py::VIDEO_SOURCE_GROUPS）

- **stock**：`pexels`、`pixabay`、`coverr`
- **AI 视频**：`metaso_minimax`（秘塔 H3）、`ofox`、`loomloom`（胜算云）、`volcengine_seedance`、`wavespeed`、`muapi`
- **AI 图片转视频**：`openai_image`（文生图 → 动态视频）
- **local**：本地上传（经 `/api/v1/video_materials` 或 material_upload.py 校验）

### 7.4 字幕（subtitle_provider）

- `edge`（默认，免 GPU）：用 TTS 时间轴直接写 SRT，不含正确时间戳修复已合入（a76b61d）
- `whisper`：faster-whisper（[whisper] 配置），模型优先取本地 `models/whisper-*` 目录；`correct()` 用脚本纠错；edge 失败不自动降级

### 字体路径安全（subtitle 渲染，2026-09 新增）

`font_name` 由 API 客户端直接提交，两级校验必须保留：
1. **API 入口预检**（`video.py::create_task`）：`stop_at="video"` 且 `subtitle_enabled` 时，用 `file_security.resolve_path_within_directory(utils.font_dir(), font_name)` 在创建任务前返回 400；
2. **渲染层兜底**（`video.py::generate_video`）：WebUI/CLI/内部调用绕过 API 入口，渲染前同样强制校验字体留在 `resource/fonts` 内（阻断绝对路径、`../` 穿越与指向目录外的符号链接）。

### 7.5 AI 配乐（bgm_type）

- 本地类：`random`（resource/songs + storage/bgm）、`local`（指定 bgm_file）
- AI 类：`sonilo`（.m4a）、`elevenlabs`（.mp3）；需 `video_music_prompt`；失败自动降级为无 BGM 并记录 warning。
- `task.py::_VIDEO_MUSIC_PROVIDERS` 是注册点；新供应商实现 `is_enabled() / generate_bgm(video_path, output_path, video_duration, prompt)`。

---

## 8. WebUI（webui/Main.py，Streamlit）

- 主界面结构：生成表单（主题/脚本/关键词/画幅/素材源/音色/字幕/BGM）+ 任务历史（扫描 storage/tasks）+ 恢复对话框 + 生成中任务实时进度与日志。
- 设置对话框 6 个 tab：**中间配置** / **素材 API** / **发布（Upload-Post 全套）** / **日志** / **Key 备份** / **缓存管理**。
- 关键控件需用稳定版包装：`stable_selectbox / grouped_selectbox / stable_segmented_control`（防止 Streamlit rerun 闪烁）。
- i18n：`tr()` 从 `webui/i18n/` 取文案，支持 15 个 locale（含新增 ca-ES 加泰罗尼亚语，可作脚本语言）；切换语言时保留已输入的主题/脚本/关键词草稿。
- 缓存管理注意：`st.cache_data` 会 pickle 返回值，**只能缓存 plain dict**，不得返回自定义类实例（如 `VideoCacheStats`）——否则源码 watcher 清掉 `sys.modules` 后旧 Dialog 持有旧类引用会抛 PicklingError（3d5f4e4）。
- 特殊流程：VoxCPM 参考音频上传（本地 Whisper 转逐字稿草稿）；LoomLoom 报价确认流（quote→用户确认→传入 `start()`）。

## 9. CLI（cli.py）

- 批量 JSON/JSONL 清单驱动；参数优先级：命令行 > `[ui]` 配置 > 内置默认。
- 可 `allow_server_file_input=True` 开放服务端文件输入（任务目录内白名单解析）。

---

## 10. 测试与安全机制

### 测试
- 入口：`test_main.py`；`test/services/` 覆盖：controller（auth/video/llm/ping）、task/task_manager/task_artifacts、material/material_cache/material_openai_image/material_upload、video/clip_speed/视频特效、voice 系列、llm/fluxionai、bgm/sonilo/elevenlabs_music、muapi/ofox/metaso_minimax/loomloom/volcengine_seedance/twelvelabs、upload_post/youtube_audience、schema/config/subtitle、webui 全系列（headless task actions、provider 设置页、voice preview、task restore/transfer）、ASGI CORS 与静态文件。
- 测试资源在 `test/resources/`。

### 文件与安全工具（改动必须复用）
- `app/utils/file_security.py`：路径白名单解析、防穿透
- `material_upload.py` / `bgm.py`：`sanitize_upload_filename`（UUID 重命名、C1 控制符/双向字符/Win32 保留名/超换算字符拒绝）
- 上传大小上限：BGM 30MB、视频 200MB、图片 20MB、Sonilo 代理 300MB

---

## 11. 二次开发路线指南（常见扩展步骤）

### 新增一个 AI 文生视频 Provider
1. 新建 `app/services/<name>.py`：常量表 + 异常类（携带远端 task_id）+ `is_enabled()` + 提交/轮询/下载函数（仿 `ofox.py` / `muapi.py`）
2. `material.py` 分发注册
3. `task.py` 加 preflight `is_enabled()` 检查
4. `webui/Main.py::VIDEO_SOURCE_GROUPS` 加项
5. `app/models/schema.py` / `config.toml(config.example.toml)` 加配置三键 `<id>_api_key/_base_url/_model_name`
6. `test/services/` 加测试；如需缓存，遵守 `mid-<md5>.mp4` 命名让 cache_manager 可管理

### 新增一个 BGM（视频配乐）Provider
实现 `is_enabled()` / `generate_bgm(video_path, output_path, video_duration, prompt)`（可选 `MAX_PROMPT_LENGTH` / `validate_generation_access()`），注册进 `task.py::_VIDEO_MUSIC_PROVIDERS`；schema 加 `bgm_type` 枚举值与专用 prompt 字段。

### 新增一个 LLM Provider
`app/models/llm_provider.py` Registry 加 `LLMProviderSpec`（OpenAI 兼容即零代码，如 cheaperinference）；config.toml 加 `<id>_api_key/_base_url/_model_name` 三键；协议不同才在 `llm.py` 写新 adapter。

### 新增 TTS Provider
`voice.py` 加适配器 + `tts()` 分发；若对应 WebUI 选项，需同步 `webui/Main.py` 的 tts_server 列表与 i18n。

### 配置写入
一律用 config 模块非阻塞 API（见 §4 运行时并发安全）。

### 其他约定
- 任务产物：`storage/tasks/<task_id>/`；`script.json` 只通过 `task_artifacts.py` 原子读写。
- 任务队列上限：`max_concurrent_tasks=5`、`max_queued_tasks=100`（InMemory/RedisManager，由 `enable_redis` 切换）。Redis 队列出队循环会**原地丢弃不可用条目**（JSON 损坏、func 不在 FUNC_MAP、kwargs/args 形态非法、VideoParams 校验失败），并把能读到 task_id 的条目标记为失败（633dbb0）——不要让异常逃出 dequeue 带崩工作线程。
- CORS 默认同源；放开用环境变量 `CORS_ALLOWED_ORIGINS`（逗号分隔）。配置项会被折叠成浏览器 Origin 规范形式（小写 host、省略默认端口、去尾斜杠），无法匹配任何 Origin 的畸形条目被丢弃并告警（e0c9cb7）。
- FFmpeg 路径探测统一收口在 `task.py`（API/CLI/WebUI 三路径共享），不要在其他入口重复探测。

---

## 12. docs 目录

- `docs/skill/SKILL.md` + `mpt_agent.py`：Agent Skill v1.3.2（默认中文 9:16、Pexels、Edge TTS）
- `docs/loomloom/`：两个 API 模板 JSON（video-materials、script-candidates）
- `docs/voice-list.txt`：Edge TTS 音色清单
- `docs/MoneyPrinterTurbo.ipynb`：Colab 示例

---

## 13. 常见坑（重要提醒）

1. **config.toml 运行时写入锁**：长任务运行期间直接写配置会死锁；必须走非阻塞 API。
2. **edge 字幕失败不自动降级 whisper**：这是刻意设计（避免下载大模型），新代码不要"好心"自动降级。
3. **AI 视频提交后疑似模糊响应绝不重试**：上游任务可能已计费。
4. **stop_at 阶段语义**：成功提前结束也返回 100% 进度COMPLETE，消费方需看 `videos` 是否为空/`stop_at`。
5. **批量任务的素材去重**：只在 pexels/pixabay/coverr/local 下做；结果是 `batch_materials_reused` warning + `material_selections` 记录。
6. **字幕位置默认值取自 `[ui]` 配置**而非 schema 默认，改动时注意两者同步。
7. **bgm `""` 语义是"禁用 BGM"**，`None` 才是"走 bgm_type 解析"，勿混淆。
8. **Docker 构建（国内网络）**：基础镜像拉取可能超时，可先从 `docker.m.daocloud.io/library/python:3.11-slim-bullseye` 拉取后重新打 tag `python:3.11-slim-bullseye`，并加 `--build-arg DOCKER_BUILD_MIRROR=china`。
9. **Win 上传文件名安全**：改 `material_upload.py` / `bgm.py` 时保持 `sanitize_upload_filename` 白名单同步。
10. **font_name 必须过目录边界校验**：API 入口预检与渲染层兜底是两级防线（见 §7.4 字体路径安全），新调用路径不得跳过 `file_security.resolve_path_within_directory(utils.font_dir(), ...)`。

---

生成于 2026-09-20，基于 git HEAD（a76b61d）；2026-09-26 增量更新至 HEAD（ad5496f，覆盖 9 个新提交：字体路径校验、Cheaper Inference provider、Groq 默认模型替换、Catalan i18n、subtitle_enabled bool 解析、CORS 规范化、Redis 队列脏数据跳过、WebUI 缓存 pickle 修复等）。升级上游后如有大改，重新生成此文档。
