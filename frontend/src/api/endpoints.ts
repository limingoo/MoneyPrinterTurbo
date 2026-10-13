import { get, post, del, upload, postForm, request } from './client'
import type {
  AudioOnlyRequest,
  ScriptRequest,
  SubtitleOnlyRequest,
  TaskListData,
  TaskStatus,
  TermsRequest,
  VideoParams,
  FileInfo,
} from './types'

/** 创建视频生成任务（带自定义配音上传：params JSON + 音频文件） */
export function createVideoWithCustomAudio(
  params: VideoParams,
  audio: File,
): Promise<{ task_id: string }> {
  const form = new FormData()
  form.append('params', JSON.stringify(params))
  form.append('file', audio)
  return postForm('/api/v1/videos/custom_audio', form)
}

/** 创建视频生成任务 */
export function createVideo(params: VideoParams): Promise<{ task_id: string }> {
  return post('/api/v1/videos', params)
}

/** 分页获取任务列表 */
export function getTasks(page = 1, pageSize = 20): Promise<TaskListData> {
  return get(`/api/v1/tasks?page=${page}&page_size=${pageSize}`)
}

/** 查询单个任务状态 */
export function getTask(taskId: string): Promise<TaskStatus> {
  return get(`/api/v1/tasks/${taskId}`)
}

/** 删除任务 */
export function deleteTask(taskId: string): Promise<unknown> {
  return del(`/api/v1/tasks/${taskId}`)
}

/** AI 生成视频文案 */
export function generateScript(body: ScriptRequest): Promise<{ video_script: string }> {
  return post('/api/v1/scripts', body)
}

/** AI 生成素材搜索关键词 */
export function generateTerms(
  body: TermsRequest,
): Promise<{ video_terms: string[] }> {
  return post('/api/v1/terms', body)
}

/** 仅生成配音 */
export function createAudio(
  body: AudioOnlyRequest,
): Promise<{ task_id: string }> {
  return post('/api/v1/audio', body)
}

/** 仅生成字幕 */
export function createSubtitle(
  body: SubtitleOnlyRequest,
): Promise<{ task_id: string }> {
  return post('/api/v1/subtitle', body)
}

/** 获取本地背景音乐列表 */
export function getBgmList(): Promise<{ files: FileInfo[] }> {
  return get('/api/v1/musics')
}

/** 上传背景音乐 */
export function uploadBgm(file: File): Promise<{ file: string }> {
  return upload('/api/v1/musics', file)
}

/** 获取本地视频素材列表 */
export function getLocalMaterials(): Promise<{ files: FileInfo[] }> {
  return get('/api/v1/video_materials')
}

/** 上传本地视频素材 */
export function uploadMaterial(file: File): Promise<{ file: string }> {
  return upload('/api/v1/video_materials', file)
}

/** 获取音色目录（前端静态资源，由 public/voices.json 提供，零后端依赖） */
export async function getVoices(): Promise<{ voices: string[] }> {
  const res = await fetch('/voices.json')
  if (!res.ok) return { voices: [] }
  const voices = (await res.json()) as string[]
  return { voices }
}

/** 连通性检测（/ping 返回纯字符串，不走统一信封） */
export async function ping(): Promise<boolean> {
  try {
    const res = await fetch('/ping')
    return res.ok
  } catch {
    return false
  }
}

/* ================= 配置 ================= */

export interface SecretField {
  has_value: boolean
  masked: string
}

export interface LLMProviderMeta {
  id: string
  label: string
  default_model: string
  default_base_url: string
  requires_api_key: boolean
  requires_base_url: boolean
  requires_model_name: boolean
  show_api_key: boolean
  show_base_url: boolean
}

export interface LLMConfigView {
  provider: string
  providers: LLMProviderMeta[]
  values: {
    api_key: SecretField
    base_url: { value: string }
    model_name: { value: string }
  }
  current_spec: {
    default_model: string
    default_base_url: string
    requires_api_key: boolean
    requires_base_url: boolean
    requires_model_name: boolean
    show_api_key: boolean
    show_base_url: boolean
  }
}

export interface MaterialsConfigView {
  [key: string]: { count: number; masked: string }
}

export interface ConfigView {
  llm: LLMConfigView
  materials: MaterialsConfigView
  tts: { siliconflow_api_key: SecretField }
  cloned_voices: ClonedVoiceEntry[]
}

export interface ClonedVoiceEntry {
  id: string
  label: string
  voice_name: string
  uri: string
  provider: string
  tts_model?: string
  created_at?: string
}

export interface LLMConfigUpdate {
  provider?: string
  api_key?: string
  base_url?: string
  model_name?: string
}

export interface MaterialsConfigUpdate {
  pexels_api_keys?: string
  pixabay_api_keys?: string
  coverr_api_keys?: string
  wavespeed_api_keys?: string
}

export function getConfig(): Promise<ConfigView> {
  return get('/api/v1/config')
}

export function updateConfig(body: {
  llm?: LLMConfigUpdate
  materials?: MaterialsConfigUpdate
}): Promise<{ updated: string[] }> {
  return request('/api/v1/config', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function testLLM(): Promise<{ ok: boolean; error?: string; sample_terms?: string[] }> {
  return post('/api/v1/config/test_llm')
}

/** 拉取提供商的模型列表（动态 /models 优先，静态清单回退） */
export function getProviderModels(
  provider: string,
): Promise<{ provider: string; models: string[]; source: string }> {
  return get(`/api/v1/config/models?provider=${encodeURIComponent(provider)}`)
}

/* ================= 素材库 ================= */

export interface MaterialLibraryEntry {
  file: string
  kind: 'video' | 'image' | 'other'
  size: number
  status: string
  tags: string[]
  tags_en?: string[]
  duration?: number
  metrics?: Record<string, number>
  created_at?: string
  status_reason?: string
  beauty_related?: boolean
  from?: string
}

export interface TrashEntry {
  file: string
  kind: 'video' | 'image' | 'other'
  status: string
  status_reason: string
  tags: string[]
  size: number
  duration?: number
}

export function getMaterialTrash(): Promise<{ files: TrashEntry[] }> {
  return get('/api/v1/material_trash')
}

export function purgeMaterialFile(filename: string): Promise<{ purged: string }> {
  return del(`/api/v1/material_purge?filename=${encodeURIComponent(filename)}`)
}

/** 素材预览/缩略图 URL（本机部署默认未启用 API 鉴权；若启用需在 img/video 标签场景另行处理） */
export function materialPreviewUrl(filename: string): string {
  return `/api/v1/material_preview?filename=${encodeURIComponent(filename)}`
}

export function materialThumbUrl(filename: string): string {
  return `/api/v1/material_thumb?filename=${encodeURIComponent(filename)}`
}

export interface IngestJob {
  done: boolean
  filename: string
  events: {
    file?: string
    type?: string
    status?: 'ok' | 'low_quality' | 'duplicate' | 'irrelevant' | 'rejected' | 'error'
    reason?: string
    restorable?: boolean
  }[]
  summary?: Record<string, number>
}

export function startMaterialIngest(filename: string): Promise<{ job_id: string }> {
  return post('/api/v1/material_ingest', { filename })
}

export function getMaterialIngest(jobId: string): Promise<IngestJob> {
  return get(`/api/v1/material_ingest/${jobId}`)
}

export function getMaterialLibrary(): Promise<{ files: MaterialLibraryEntry[] }> {
  return get('/api/v1/material_library')
}

export function updateMaterialTags(
  filename: string,
  tags: string[],
): Promise<{ file: string; tags: string[] }> {
  return request('/api/v1/material_tags', {
    method: 'PUT',
    body: JSON.stringify({ filename, tags }),
  })
}

export function deleteMaterialFile(filename: string): Promise<{ removed: string }> {
  return del(`/api/v1/material_library?filename=${encodeURIComponent(filename)}`)
}

export function restoreMaterialFile(filename: string): Promise<unknown> {
  return post('/api/v1/material_restore', { filename })
}

export interface MaterialCheckResult {
  covered: string[]
  missing: string[]
  matched: Record<string, string[]>
}

export function checkMaterials(
  videoTerms: string[],
): Promise<MaterialCheckResult> {
  return post('/api/v1/materials_check', { video_terms: videoTerms })
}

export interface RemixResult {
  created: { file: string; term: string; from: string; transform: string }[]
  failed: { term: string; error: string }[]
  reason?: string
}

export function remixMaterials(
  videoTerms: string[],
  videoAspect: string,
): Promise<RemixResult> {
  return post('/api/v1/materials_remix', {
    video_terms: videoTerms,
    video_aspect: videoAspect,
  })
}

/* ================= 分镜 ================= */

export function makeStoryboard(body: {
  video_subject: string
  video_script: string
  video_language?: string
  max_shots?: number
}): Promise<{ shots: StoryboardShot[] }> {
  return post('/api/v1/storyboard', body)
}

/* ================= 拍摄任务清单（视频生成任务的素材缺口） ================= */

export interface StoryboardShot {
  text: string
  term: string
  title?: string
  brief?: string
}

export interface ShootNeed {
  id: string
  task_id: string
  term: string
  title: string
  brief: string
  status: 'open' | 'resolved'
  filename?: string
  requested_at?: string
}

/** 登记某生成任务的拍摄缺口（人话简报） */
export function addShootNeeds(
  taskId: string,
  items: { term: string; title: string; brief: string }[],
): Promise<{ added: number }> {
  return post('/api/v1/shoot_needs', { task_id: taskId, items })
}

/** 拉取拍摄需求（不传 task_id 返回全部） */
export function getShootNeeds(taskId?: string): Promise<{ needs: ShootNeed[] }> {
  return get(taskId ? `/api/v1/shoot_needs?task_id=${encodeURIComponent(taskId)}` : '/api/v1/shoot_needs')
}

/** 手动补齐一条需求（素材上传入库也会自动核销） */
export function resolveShootNeed(id: string, filename = ''): Promise<{ need: ShootNeed }> {
  return post('/api/v1/shoot_needs/resolve', { id, filename })
}

/* ================= 声音克隆 ================= */

export function cloneVoice(
  name: string,
  text: string,
  audio: File,
): Promise<{ id: string; label: string; voice_name: string }> {
  const form = new FormData()
  form.append('name', name)
  form.append('text', text)
  form.append('file', audio)
  return postForm('/api/v1/voice_clone', form)
}

export function getClonedVoices(): Promise<{ voices: ClonedVoiceEntry[] }> {
  return get('/api/v1/voice_clones')
}

export function deleteClonedVoice(id: string): Promise<{ removed: string }> {
  return post('/api/v1/voice_clones/delete', { id })
}

/** 保存 SiliconFlow API Key（声音克隆与克隆音色合成共用） */
export function saveSiliconFlowKey(key: string): Promise<{ updated: string[]; saved: boolean }> {
  return request('/api/v1/config', {
    method: 'PUT',
    body: JSON.stringify({ tts: { siliconflow_api_key: key } }),
  })
}
