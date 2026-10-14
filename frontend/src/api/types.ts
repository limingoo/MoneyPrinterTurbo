/** 与后端 app/models/schema.py 对齐的 API 类型定义 */

export interface MaterialInfo {
  provider: string
  url: string
  duration?: number
  source_info?: Record<string, unknown> | null
}

export type VideoAspect = '16:9' | '9:16' | '1:1'
export type VideoFitMode = 'cover' | 'contain'
export type VideoConcatMode = 'random' | 'sequential'
export type VideoTransitionMode =
  | 'Shuffle'
  | 'FadeIn'
  | 'FadeOut'
  | 'SlideIn'
  | 'SlideOut'
  | 'ZoomIn'
  | 'ZoomOut'
export type SubtitlePosition =
  | 'top'
  | 'center'
  | 'bottom'
  | 'two_thirds_bottom'
  | 'custom'
export type SubtitleDisplayMode = 'sentence' | 'word_by_word'
export type SubtitleAnimation = 'none' | 'pop_spring'

export interface VideoParams {
  video_subject: string
  video_script: string
  video_terms?: string[] | string | null
  video_aspect?: VideoAspect
  video_fit_mode?: VideoFitMode
  video_concat_mode?: VideoConcatMode
  video_transition_mode?: VideoTransitionMode | null
  video_clip_duration?: number
  video_clip_speed?: number
  match_materials_to_script?: boolean
  video_count?: number
  video_source?: string
  video_materials?: MaterialInfo[] | null
  video_language?: string
  voice_name?: string
  voice_volume?: number
  voice_rate?: number
  bgm_type?: string
  bgm_file?: string
  bgm_volume?: number
  video_music_prompt?: string
  subtitle_enabled?: boolean
  subtitle_position?: SubtitlePosition
  subtitle_display_mode?: SubtitleDisplayMode
  subtitle_animation?: SubtitleAnimation
  custom_position?: number
  font_name?: string
  text_fore_color?: string
  text_background_color?: boolean | string
  rounded_subtitle_background?: boolean
  font_size?: number
  stroke_color?: string
  stroke_width?: number
  n_threads?: number
  paragraph_number?: number
  video_script_prompt?: string
  custom_system_prompt?: string
}

/** 任务状态码，与 app/models/const.py 对齐 */
export const TASK_STATE = {
  FAILED: -1,
  COMPLETE: 1,
  PROCESSING: 4,
} as const

export interface TaskStatus {
  task_id: string
  state: number
  progress: number
  stage?: string
  videos?: string[]
  combined_videos?: string[]
  audio_file?: string
  subtitle_path?: string
  failed_stage?: string
  error?: string
  params?: VideoParams
  created_at?: number
}

export interface TaskListData {
  tasks: TaskStatus[]
  total: number
  page: number
  page_size: number
}

export interface FileInfo {
  name: string
  size: number
  file: string
}

export interface ApiEnvelope<T> {
  status: number
  data: T
  message?: string
}

export interface ScriptRequest {
  video_subject: string
  video_language?: string
  paragraph_number?: number
  video_script_prompt?: string
  custom_system_prompt?: string
}

export interface TermsRequest {
  video_subject: string
  video_script: string
  amount?: number
  match_materials_to_script?: boolean
}

/** 仅生成配音（POST /api/v1/audio） */
export interface AudioOnlyRequest {
  video_script: string
  video_language?: string
  voice_name?: string
  voice_volume?: number
  voice_rate?: number
  bgm_type?: string
  bgm_file?: string
  bgm_volume?: number
}

/** 仅生成字幕（POST /api/v1/subtitle） */
export interface SubtitleOnlyRequest extends AudioOnlyRequest {
  subtitle_position?: string
  subtitle_display_mode?: 'sentence' | 'word_by_word'
  subtitle_animation?: 'none' | 'pop_spring'
  font_name?: string
  text_fore_color?: string
  text_background_color?: boolean | string
  rounded_subtitle_background?: boolean
  font_size?: number
  stroke_color?: string
  stroke_width?: number
}
