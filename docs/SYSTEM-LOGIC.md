# MoneyPrinterTurbo 系统逻辑详解（由粗到细）

> 本文档从业务到代码逐层展开整个系统的运行逻辑。与PROJECT-HANDBOOK.md 互为补充：Handbook 讲"怎么二次开发"，本文讲"系统到底是怎么跑起来的"。
>
> 基于代码快照 2026-09-26（git HEAD ad5496f）。行号引用以当前代码为准。

---

# 第 0 层：一句话总览

用户给一个**主题**，系统自动完成"写文案 → 找画面线索 → 配音 → 字幕 → 找视频素材 → 剪辑合成 → 可选自动发布"，产出若干条带配音、字幕、配乐的短视频。**配音是全局的时间基准**：字幕取自配音时间轴，视频素材被裁剪/循环到配音长度，最终把三者压在一条时间线上。

---

# 第 1 层：三大入口，同一条流水线

```
WebUI (Streamlit, :8501)  ──┐
CLI (cli.py, JSON/JSONL)  ──┼──>  task.start()  ──>  _run_pipeline()   [task.py:1376]
API  (FastAPI,   :8080)   ──┘         (队列管理器调度)
```

- 三个入口最终都汇到 `app/services/task.py::start()`（task.py:1703），由任务管理器排队执行。
- 所有入口共享同一 preflight（FFmpeg 探测在 task.py:1492 统一收口），行为一致。
- 任务状态统一写入 `state.py`（内存或 Redis），API/WebUI 从这里轮询进度。

**任务队列**（app/controllers/manager/）：
- `max_concurrent_tasks=5`（并发上限）、`max_queued_tasks=100`（排队上限），队列满时创建任务直接返回 429（base_manager.py:42）。
- Redis 模式 `enable_redis=true` 时用 rpush/lpop；出队循环会原地丢弃不可用条目（JSON 损坏、func 不在 FUNC_MAP、VideoParams 校验失败），并给能读到 task_id 的条目标记失败（redis_manager.py，633dbb0）。

**鉴权**（app/controllers/base.py）：
- `config.app["api_key"]` 非空 → 所有 `/api/v1/*` 强制 `x-api-key` 头（恒定时间比较）；为空 → 全部免鉴权。`/ping` 恒免鉴权。
- `x-task-id` 头仅用于日志追踪。

---

# 第 2 层：流水线主干（task.py::_run_pipeline）

进度刻度：`5 preflight → 10 script → 20 terms → 30 audio → 40 subtitle → 50 素材 → 合成期 50→100`。

每阶段完成且 `stop_at` 命中时：任务立即标记 `COMPLETE + progress=100`，返回该阶段的中间产物（脚本/关键词/音频/字幕/素材），`videos` 为空——**消费方必须看 `videos` 字段判断是否有成片**（task.py:1512-1611 各分支）。

## 2.0 preflight（进度 5，task.py:1390-1498）
按序校验，先失败先报：
1. AI 视频源 Key 检查（volcengine_seedance / ofox / metaso_minimax / muapi / openai_image，仅在视频源命中时）
2. AI 配乐供应商检查（bgm_type ∈ sonilo/elevenlabs 时）：`is_enabled()` + prompt 长度上限 + 可选 `validate_generation_access()` 账号前置检查（task.py:1451-1486）
3. FFmpeg 可用性（`stop_at ∈ {script, terms}` 时跳过，这两个阶段不产出音视频）

任何 preflight 失败 → `_mark_task_failed(task_id, "preflight", ...)`（task.py:243），带 `failed_stage` 字段。

## 2.1 脚本生成（5→10）
`generate_script()`（task.py:292）：
- `video_script` 非空 → 直接用（跳过 LLM）
- 否则 `llm.generate_script(video_subject, language, paragraph_number, video_script_prompt, custom_system_prompt)`
- 失败标记 `failed_stage="script"`

## 2.2 关键词/搜索词生成（10→20）
`generate_terms()`（task.py:313）：
- `video_source=="local"` → 跳过（本地素材不需要搜索词）
- `video_terms` 用户已提供 → 按逗号/列表切分
- 否则 LLM 生成，**amount 逻辑**：`match_materials_to_script=True` 时 amount=8 且要求按脚本叙事顺序生成；否则 amount=5（task.py:320-325）
- 可选 TwelveLabs Marengo 语义重排（仅非顺序匹配模式；无 Key 时 no-op）（task.py:344-350）

然后原子写入 `storage/tasks/<task_id>/script.json`（`task_artifacts.py::save_script_data`，临时文件 + rename），包含 script、terms、素材 provenance 等字段，**所有 script.json 读写必须走 task_artifacts 模块**。

## 2.3 配音生成（20→30）
`generate_audio()`（task.py:482）按三级优先级：

1. **custom_audio_file**：`resolve_custom_audio_file()`（task.py:364）做路径白名单校验——HTTP 调用只允许任务目录内文件；CLI 显式传 `allow_server_file_input=True` 才允许读服务端文件（且相对路径不得逃出项目根）。使用自定义音频时 **sub_maker=None**（没有 TTS 时间轴）。
2. **WebUI 试听缓存**：`_resolve_reusable_voice_preview()`（task.py:422）——重新核对 script/voice_name/voice_rate/voice_volume 完全一致、音频文件在任务目录内、时长为有效正数，任何不一致就回退普通 TTS。
3. **TTS 合成**：`voice.tts()` 写 `audio.mp3`，返回 sub_maker（字幕时间轴载体）。

**时长计算关键细节**（task.py:552-565）：`audio_duration = ceil(实际文件时长)`——用 `voice.get_audio_duration(audio_file)` 读文件，而**不是** `sub_maker.cues[-1].end`（最后一个词边界，Edge TTS 尾部固定有约 0.88s 余音，短文案低估比例可达 19%）。低估会连锁影响付费素材量和 BGM 时长。

## 2.4 字幕生成（30→40）
`generate_subtitle()`（task.py:580）：

- `subtitle_enabled=False` 或 provider 为空 → 返回 ""（无字幕）
- **edge（默认）**：`voice.create_subtitle()` 用 TTS 时间轴（sub_maker）+ 脚本文案按标点切行，逐行对齐生成 SRT。**edge 失败不会降级 whisper**（避免意外下载数 GB 模型），只留无字幕视频 + warning（task.py:619-628）
- **whisper**：`subtitle.create()` 用 faster-whisper 转写音频（模型优先取本地 `models/whisper-{size}`），非逐词模式时再用 `subtitle.correct()` 按脚本纠错（task.py:630-638）
- `word_by_word` 显示模式：两条路径都输出逐词单条字幕

## 2.5 素材获取（40→50）——见第 3 层详解
## 2.6 最终合成（50→100）——见第 4 层详解

## 2.7 收尾与跨平台发布（task.py:1639-1700）
- 全部产物（videos/combined_videos/script/terms/audio/subtitle_path/materials/warnings）一次性 update_task 为 COMPLETE。
- 若 `upload_post` 已配置且 `auto_upload=true` 且有平台列表 → `cross_post_state=PENDING`，然后 **异步** `_schedule_cross_post()`（线程池）：每个平台先 LLM `generate_social_metadata()`（title/caption/hashtags，失败降级为启发式 fallback：主题当 title、脚本当 caption），再逐视频发布。
- 发布失败不影响已生成的成片；启动时 `recover_interrupted_cross_posts()` 回收中断任务。

**失败语义**：任何阶段失败 → `_mark_task_failed(task_id, failed_stage, error)`，任务 state=-1（失败），进度保持已有值。

---

# 第 3 层：素材获取详解（material.py）

入口 `download_videos()`（material.py:1686），按 `video_source` 分发为两大流派：

## 3.1 流派 A：库存搜索（pexels/pixabay/coverr）——"先搜索后挑"
1. **搜索**：每个关键词调 provider API（需要画幅过滤 `_filter_materials_by_aspect` + 最短时长 ≥ max_clip_duration），结果经 24h 磁盘缓存（`material_cache.py`，`storage/cache_videos`，TTL 24h、每小时清理）
2. **汇总**：所有关键词的候选去重合并成大列表；`random` 模式打乱
3. **顺序下载**：逐条 `save_video()` 下载，累计有效时长 `min(max_clip_duration, item.duration)`，**超过 `audio_duration` 即停**（material.py:1860-1866）
4. **失败容错**：单条下载失败跳过继续；每条记录 provenance（provider/URL/asset_id）写 script.json

## 3.2 流派 B：顺序匹配（match_materials_to_script=True）
`_download_videos_by_script_order()`（material.py:2402）：为避免"第一个关键词返回太多素材把后面的脚本主题挤出时间线"，按关键词**轮询**下载：第 1 轮取每个关键词的第 1 个候选，第 2 轮取第 2 个候选……直到凑够时长。配合 terms 按脚本顺序生成，画面顺序贴近文案顺序。

## 3.3 流派 C：AI 生成源（wavespeed/volcengine_seedance/ofox/muapi/metaso_minimax/openai_image）——"按需付费"
统一语义（material.py:1724-1794）：
- **逐段按需生成**：每个关键词 ≈ 一段画面，生成一段 → 下载 → 累计有效时长 → **够 `audio_duration` 立即停**，不为用不到的片段付费
- **不参与 24h 缓存**：AI 产物 URL 是会过期的签名地址，且缓存会让不同任务拿到同一画面
- **耗时产物可恢复**：所有 AI 源异常统一携带远端 task_id（如 `ofox_task_id`、`muapi_task_id`），失败状态里带 details 供控制台找回**已计费**任务
- **疑似模糊响应绝不重试**（任务可能已计费）
- openai_image 特殊：文生图按张计费，图片本地渲染成"缓慢放大"视频片段（`render_image_zoom_video`，video.py:1533，每秒约 3% 放大）

## 3.4 流派 D：local 本地素材
`video.preprocess_video()`（video.py:1567）校验+预处理：
- 路径必须解析在 `storage/local_videos` 内（防任意文件读取）
- 分辨率门槛：低于最小尺寸（允许少量容差）的素材剔除
- 图片 → 渲染为 zoom 动效视频片段；视频 → 直接用

## 3.5 分镜对齐（local 源的实际取画逻辑）
本地素材源不经过关键词搜索，画面与文案是否对得上完全由这一层决定。

`task.py::build_shot_plan_inputs`（local + `match_materials_to_script`）：
1. **拆分镜**：LLM 把文案拆成 {text, term, title, brief}（`storyboard.py`）。
   失败只记日志并退回固定步长拼接，绝不因此中断任务。
2. **还原库内标签**：按 `shot_alignment.source_name_of()` 把预处理后的路径
   映射回库内原始文件名（图片已渲染成 `<原名>.zoom-<hash>.mp4`），从索引取
   标签/时长。
3. 产出 `build_plan_inputs`，在 `generate_final_videos` 里按 `video_count`
   逐条渲染成 `ClipPlan`（第 2 条起换候选优先级，避免多条视频画面一致）。

`shot_alignment.build_clip_plan` 的规划规则：
- 每个镜头用字幕时间轴算出应占时长（`shot_windows` 累积 cue 到覆盖镜头
  句子的 80%）；字幕缺失时按镜头数均分。
- 镜头 term 与库内标签打分（`score_candidate`：精确命中 3.0 / 包含 2.0 /
  英文别名 2.5 / 文件名 1.0），**0 分表示不相关**。
- 排序扣分：标签家族重合度（`_FAMILY_OVERLAP_PENALTY`）、家族累计占用秒数
  （`_FAMILY_SECOND_PENALTY`，压住"连续三张同款照片"）、同素材复用次数。
  扣分要大于"精确 vs 包含"的 1.0 分差，同款才会让位给风格不同但同样贴题的
  素材。
- 一个镜头可吃多条素材（按轮次取，`take()` 接着上次的结束点往后取）；素材
  不够时同一条最多完整走 `_MAX_SOURCE_PASSES` 遍，而不是把一个片段无限
  `itertools.cycle`（实测旧实现 69 秒成片里同一条 5 秒片段原样播了 4 次）。
- 完全无命中的镜头用兜底素材填上（不留空白），并登记为拍摄需求。
- 计划落盘到 `script.json` 的 `shot_plan`，事后可逐镜头核对。

`combine_videos(clip_plan=...)`：计划存在时跳过场景边界探测与去重重排
（顺序本身就是语义），按 `target_duration` 渲染；计划全部不可用时退回旧的
固定步长拼接。

## 3.6 批量任务的素材去重（batch，task.py:883-997）
`video_count>1` 且源 ∈ {pexels, pixabay, coverr, local} 时启用 `allocate_batch_materials`：
- 维护跨视频的 `source_usage` 使用计数
- `_prioritize_unique_source_clips()`（video.py:203）：random 模式下每个源文件先只出**最长片段**做主选，其余作兜底；已用过的源排到后面
- 第 N 条视频优先选用未复用素材；确实不够就复用并记 warning `batch_materials_reused` + `material_selections` 明细写入 script.json
- AI 生成源**不做**批量分配（生成的视频本来就是唯一的）

---

# 第 4 层：画面拼接详解（video.py::combine_videos）

这是"配音怎么和视频适配"的核心，video.py:744-979。

## 4.1 确定需要多少画面
```
required_video_duration = audio_duration + 0.1s 安全余量   (video.py:189, _VIDEO_DURATION_SAFETY_MARGIN at 79)
```
余量的意义：素材实际时长零头被截断、ffmpeg concat 舍入时，保证总长仍覆盖配音，避免音频末尾因帧舍入出现黑屏、卡顿或最后一句旁白没有画面。

## 4.2 切片（video.py:774-850）
- 每条源视频按 `source_clip_duration = max_clip_duration × clip_speed` 切成片段
  - 为什么反推：变速发生在后。MoviePy 0.5x 放 1.5s 源 → 3s 成片片段。固定 5s 读取再慢放下一段会从第 5s 开始，跳过中间画面。反推保证**源时间线连续无重叠**（video.py:779-786）
- `random` 拼接模式：每条源视频只取第 1 段；`sequential`：一条视频的全部段落都保留

## 4.3 片段处理循环（video.py:837-928），每个片段依次：
1. 打开 → subclip → 按 `clip_speed` 变速
2. **画幅归一** `_fit_clip_to_canvas`：非画布尺寸时，cover 模式放大铺满裁边，contain 模式留黑边（video.py:860-874）
3. **转场**（可选，1s）：FadeIn/FadeOut/SlideIn(随机边)/SlideOut/ZoomIn/ZoomOut/**Shuffle=随机挑一种**（video.py:876-901）。转场在变速之后应用，避免转场长度跟着变速走
4. 超过 max_clip_duration 截断兜底 → 写临时文件 `temp-clip-N.mp4`
5. `video_duration` 累计，**≥ required_video_duration 立即 break**（video.py:837）

## 4.4 不够时循环复用（video.py:935-950）
处理过的片段 `itertools.cycle` 循环补齐到 required 时长。

## 4.5 FFmpeg concat 合并（video.py:960）
- 优先 FFmpeg 无需重编码的 `-f concat` 快速拼接（`concat_video_clips_with_ffmpeg`，video.py:482，带心跳日志防长任务假死）；编解码不兼容时回退 MoviePy
- `max_duration=audio_duration`：**裁掉安全余量部分**，combined 视频长度 ≈ 配音时长

## 4.6 去重透明记录
`used_video_paths` 按"时长截断前的实际入选顺序"记录（video.py:967-974），供批量去重统计。

---

# 第 5 层：最终渲染详解（video.py::generate_video）

generate_final_videos()（task.py:883）循环 video_count 次，每次：combine_videos → （可选 AI BGM）→ generate_video。

## 5.1 AI BGM 分支（task.py:968-997）
- `bgm_file_override` 三态语义：**None=走 bgm_type 常规解析（随机/本地文件并循环铺满）、空串=禁用 BGM、路径=用供应商返回文件（不循环）**（video.py:1477-1500）
- AI 配乐 provider（sonilo/elevenlabs）失败 → **降级为无 BGM + warning**，不浪费整条已生成的任务
- 音量 ≤0 时统一跳过所有 BGM 短路（video.py:1466-1475）
- BGM 混音：音量 ×bgm_volume + 3s AudioFadeOut；混音失败返回 False，仍输出无 BGM 成片（video.py:1489-1511）

## 5.2 字幕渲染（video.py:1261-1429）
读 SRT → 每条 cue 生成一个 TextClip：
1. **换行**：`wrap_text()` 按字体真实度量计算，文本最大宽度 = 90% 画布宽；有背景时先扣掉左右 padding 再换行（video.py:1271-1285）
2. **高度预算**：字号 ×0.35 垂直 padding + 行距 ×0.25 + **每行双侧描边空间**（描边会向上下扩张，多行会逐行累积误差）（video.py:1289-1306）
3. **三种背景形态**：无背景 / 矩形背景（alpha=255 全宽）/ 圆角背景（按最长行实测宽度生成圆角底板，alpha=140，radius≥8）（video.py:1308-1398）
4. **pop_spring 动画**：显式选择后启用，基于时间的弹簧缩放变换（video.py:1404-1407，_apply_subtitle_spring_animation at 160）
5. **五种位置**：bottom(95%处)/top(5%)/two_thirds_bottom(上 1/3)/custom(百分比+夹紧边界)/center（video.py:1409-1428）

字体路径安全双保险：API 入口预检（video.py 控制器 create_task，`stop_at="video"` 且字幕启用时先 `resolve_path_within_directory(font_dir, font_name)` 返回 400）+ 渲染层兜底（video.py:1242-1247，WebUI/CLI 直调也会被拦，阻断绝对路径/`../`/符号链接逃逸）。

## 5.3 音轨合成（video.py:1431-1513）
- 旁白轨：`AudioFileClip × voice_volume`
- 叠加 BGM（若有）
- `video_clip.with_audio(...)`：**音频从 0 开始整条铺放，不做偏移**；combined 视频与配音几乎等长（concat 时按 audio_duration 截断），所以画面/配音/字幕（来自配音时间轴）三者天然对齐
- 输出前显式沿用输入采样率（取不到回退 44100Hz），减少 Docker 重采样音质波动（video.py:1515-1517）
- 编码器首选配置 codec，FFmpeg 不支持时自动回退（_write_videofile_with_codec_fallback，video.py:406）
- 所有 MoviePy reader 用 ExitStack 显式关闭——修复 Windows 文件占用与句柄泄漏（video.py:1431-1437）

---

# 第 6 层：TTS 与字幕时间轴（voice.py）

## 6.1 TTS 分发（voice.py::tts, 995）
- 脚本含停顿标签 `[停顿N]` 且音色是 Azure v1(Edge) → **分段合成** `_tts_with_pauses`（逐段合成后静音拼接）
- 含停顿标签但其他 provider → 清理标签后单次合成
- 每种 provider 一个适配器（azure v1=Edge/v2、siliconflow、gemini、mimo、minimax、elevenlabs、chatterbox、kokoro、fish_audio、voxcpm 音色克隆），`voice_name` 前缀约定路由

## 6.2 sub_maker：字幕时间轴的载体
TTS 返回 sub_maker，含每个"词边界"的时间（cues）。这不是可用字幕——需要**重排版**：

## 6.3 `create_subtitle()`（voice.py:3040）
- **句子模式**：脚本按标点切行 → 从 Edge cues 或 legacy sub_maker 结构中，把"词时间"归组到每行 → 行数必须与脚本行数完全一致，**不一致直接放弃**（宁缺勿错，voice.py:3072-3076）→ 写 SRT
- **逐词模式**：每个词独立一条
- a76b61d 修复：cue 正文里出现的"时间戳样式文本"不再被误认为新 cue 时间

## 6.4 whisper 路径（subtitle.py）
- faster-whisper 转写音频 → 词/句级时间戳
- `correct()`（subtitle.py:238）：按脚本原文矫正转写文本（模型误听修复）
- 模型优先本地 `models/whisper-{size}`，否则在线拉取

---

# 第 7 层：LLM 调用层（llm.py）

## 7.1 三种 Prompt 模板（硬编码）
| 用途 | 模板 | 位置 |
|---|---|---|
| 脚本 | `DEFAULT_SCRIPT_SYSTEM_PROMPT`（8 条约束：纯文本、无标题、直接进入正题…） | llm.py:35 |
| 关键词 | 内联 f-string（JSON 数组、1-3 个英文词、必须带主题词） | generate_terms 内 |
| 发布文案 | `build_social_metadata_prompt`（JSON：title/caption/hashtags，按平台字符上限） | llm.py:1063 |

## 7.2 Prompt 拼接（build_script_prompt，llm.py:700）
"规则"（system prompt）与"上下文"（subject/language/段落/附加要求）分开拼接——即使用户整体替换 system prompt，主题、语言、段落数也不会丢。

## 7.3 Provider 适配
28 个 provider 注册于 llm_provider.py；OpenAI 兼容的零代码接入，协议不同的写在 llm.py（azure/qwen/gemini/cloudflare_ai_gateway/litellm/claude_code/modelscope/ollama 等 adapter）。统一经 `_generate_response()`（llm.py:700 处的 build_script_prompt 不算；分发在 generX，实际响应函数 `_generate_response` llm.py:257）。

- 生成关键词返回 JSON 数组 → 解析失败自动降级“每词重试单条生成”等兜底
- 发布文案失败 → `_fallback_social_metadata()`（llm.py:1133）用主题/脚本启发式生成，保证接口永远返回可编辑的数据

---

# 第 8 层：配置与并发安全（config.py）

- `[app]` 承载几乎所有业务配置；分区对象 azure/whisper/elevenlabs/… 各自独立
- **写路径三件套**：`update_config_nonblocking()`（非阻塞写入 pending）→ `try_save_config()`（后台 flush 线程择机落盘）→ `snapshot_config_with_pending()`（读时合并 pending 快照）（config.py:91/250/168）
- `runtime_config_lock()` 任务执行期间持有，防止任务中途 provider 被换（config.py:274）
- **二次开发铁律**：任何配置写必须走非阻塞 API；直接改 `config.app[...]` 再存盘会与长任务锁互卡死锁
- `_SynchronizedConfig`（config.py:29）：dict 子类，写入时值未变化无副作用，避免无谓唤醒

---

# 第 9 层：设计不变量（改代码前必读）

1. **audio_duration 是全局尺子**：素材下载预算 = `audio_duration × video_count`（task.py:744）；视频填充目标 = audio_duration + 0.1s；BGM 时长 = audio_duration。改短时长估算会连锁少买素材。
2. **疑似模糊的 AI 响应绝不重试**（已计费）；异常必须携带远端 task_id 供恢复。
3. **edge 字幕不自动降级 whisper**（防止意外下载数 GB 模型）。
4. **sub_items 行数 ≠ script_lines 行数 → 放弃字幕**，不出错误字幕。
5. 片段随机打乱后 **按"每源最长片段优先"重排**，避免同一源视频的切片分散在时间线上被感知为重复。
6. 停顿标签只对 Azure v1 (Edge) 生效；其他 provider 会被静默清理。
7. BGM `""`=禁用、`None`=常规解析，勿混淆。
8. script.json 只能通过 task_artifacts.py 原子读写。
8b. **分镜计划优先于顺序拼接**：local 源 + match_materials_to_script 时，
   画面顺序由 ClipPlan 决定；计划不可用才退回"素材顺序 + 固定步长"。
9b. 入库归一化按**短边**封顶（`ingest_max_short_side`，默认 1080）。按长边
   封顶会把 1080x1920 竖屏降成 607x1080，成片再放大回 1080x1920，画面必糊。
9. 所有外部路径（字体/自定义音频/本地素材/BGM 上传/上传文件名）必须过 `file_security` 白名单校验。
10. 配置写入一律走非阻塞 API（见第 8 层）。

---

# 附录：一次完整任务的数据流示例

```
输入: video_subject="上海的一天", video_source="pexels", voice_name="zh-CN-XiaoxiaoNeural"
                │
[preflight]     ├─ ffmpeg ✓ / pexels key ✓
[script]   10%  ├─ llm.generate_script() → "清晨的外滩……"
[terms]    20%  ├─ llm.generate_terms(amount=5) → ["shanghai skyline", "bund morning", ...]
                ├─ script.json 原子落盘 ←(此后可恢复/审计)
[audio]    30%  ├─ voice.tts() → audio.mp3, sub_maker(词级时间轴)
                ├─ get_audio_duration(file) → 63s (ceil)
[subtitle] 40%  ├─ create_subtitle() → subtitle.srt (标点切行 × 时间轴归组)
[materials]50%  ├─ local 源：LLM 拆分镜 → 字幕时间轴 → 逐镜头选素材 → ClipPlan
[combine]       ├─ 循环 video_count:
                │   ├─ 每条素材切 5s 片段 → 变速 → 铺画布 → 转场 → temp-clip-N.mp4
                │   ├─ ffmpeg concat → combined-1.mp4 (≈63s)
                │   └─ (可选) sonilo.generate_bgm(combined-1.mp4) → bgm.m4a
[render]  100%  │   └─ generate_video(): 字幕 TextClip 叠加 + 旁白×voice_volume + BGM×0.2(3s淡出)
                │       → final-1.mp4
                │
[发布]          └─ upload_post 配置完备 → LLM 元数据 → TikTok/IG/YS 发布(异步)
```
