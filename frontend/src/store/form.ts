import type { VideoParams } from '../api/types'
import type { FileInfo } from '../api/types'

/** 创作表单的 UI 状态（比 API 参数更宽松，提交时统一转换） */
export interface CreateForm {
  // 脚本
  video_subject: string
  video_script: string
  video_terms: string
  paragraph_number: number
  video_script_prompt: string
  custom_system_prompt: string
  // 素材
  video_source: string
  video_aspect: '16:9' | '9:16' | '1:1'
  video_clip_duration: number
  video_clip_speed: number
  video_concat_mode: 'random' | 'sequential'
  video_transition: string // 'none' 或 VideoTransitionMode
  video_count: number
  local_materials: FileInfo[] // video_source=local 时选中的文件
  match_materials_to_script: boolean
  // 配音
  video_language: string
  voice_name: string
  voice_volume: number
  voice_rate: number
  // 字幕
  subtitle_enabled: boolean
  subtitle_position: string
  custom_position: number
  subtitle_display_mode: 'sentence' | 'word_by_word'
  subtitle_animation: 'none' | 'pop_spring'
  font_name: string
  font_size: number
  text_fore_color: string
  stroke_color: string
  stroke_width: number
  text_background_color: boolean
  rounded_subtitle_background: boolean
  // 音乐
  bgm_type: 'none' | 'random' | 'preset' | 'custom'
  bgm_file: string
  bgm_volume: number
  // 高级
  n_threads: number
  storyboard_align: boolean
}

export const FONT_OPTIONS = [
  'MicrosoftYaHeiBold.ttc',
  'MicrosoftYaHeiNormal.ttc',
  'STHeitiMedium.ttc',
  'STHeitiLight.ttc',
  'BeVietnamPro-Bold.ttf',
  'BeVietnamPro-Medium.ttf',
  'Charm-Bold.ttf',
  'Charm-Regular.ttf',
  'UTM Kabel KT.ttf',
]

export const LANGUAGE_OPTIONS = [
  { value: '', label: '自动检测' },
  { value: 'zh-CN', label: '中文（简体）' },
  { value: 'zh-HK', label: '中文（粤语）' },
  { value: 'en-US', label: 'English (US)' },
  { value: 'en-GB', label: 'English (UK)' },
  { value: 'ja-JP', label: '日本語' },
  { value: 'ko-KR', label: '한국어' },
  { value: 'fr-FR', label: 'Français' },
  { value: 'de-DE', label: 'Deutsch' },
  { value: 'es-ES', label: 'Español' },
  { value: 'ru-RU', label: 'Русский' },
]

export const TRANSITION_OPTIONS = [
  { value: 'none', label: '无转场' },
  { value: 'Shuffle', label: '随机转场' },
  { value: 'FadeIn', label: '渐显' },
  { value: 'FadeOut', label: '渐隐' },
  { value: 'SlideIn', label: '滑入' },
  { value: 'SlideOut', label: '滑出' },
  { value: 'ZoomIn', label: '放大' },
  { value: 'ZoomOut', label: '缩小' },
]

export const VIDEO_SOURCE_OPTIONS = [
  { value: 'pexels', label: 'Pexels' },
  { value: 'pixabay', label: 'Pixabay' },
  { value: 'coverr', label: 'Coverr' },
  { value: 'wavespeed', label: 'Wavespeed' },
  { value: 'local', label: '我的素材库' },
]

export const defaultForm: CreateForm = {
  video_subject: '',
  video_script: '',
  video_terms: '',
  paragraph_number: 1,
  video_script_prompt: '',
  custom_system_prompt: '',
  video_source: 'pexels',
  video_aspect: '9:16',
  video_clip_duration: 5,
  video_clip_speed: 1.0,
  video_concat_mode: 'random',
  video_transition: 'none',
  video_count: 1,
  local_materials: [],
  match_materials_to_script: false,
  video_language: '',
  voice_name: 'zh-CN-XiaoxiaoNeural-Female',
  voice_volume: 1.0,
  voice_rate: 1.0,
  subtitle_enabled: true,
  subtitle_position: 'bottom',
  custom_position: 70,
  subtitle_display_mode: 'sentence',
  subtitle_animation: 'none',
  font_name: 'MicrosoftYaHeiBold.ttc',
  font_size: 60,
  text_fore_color: '#FFFFFF',
  stroke_color: '#000000',
  stroke_width: 1.5,
  text_background_color: false,
  rounded_subtitle_background: false,
  bgm_type: 'random',
  bgm_file: '',
  bgm_volume: 0.2,
  n_threads: 2,
  storyboard_align: false,
}

const STORAGE_KEY = 'mpt.create_form'

export function loadForm(): CreateForm {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...defaultForm }
    const saved = JSON.parse(raw)
    const merged = { ...defaultForm, ...saved }
    // 历史存档可能保存了已下线的本地图源，回退到默认网络源
    if (!VIDEO_SOURCE_OPTIONS.some((o) => o.value === merged.video_source)) {
      merged.video_source = defaultForm.video_source
    }
    return merged
  } catch {
    return { ...defaultForm }
  }
}

export function saveForm(form: CreateForm) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(form))
  } catch {
    /* 存储不可用时忽略 */
  }
}

/** UI 表单 → API 请求体 */
export function toParams(form: CreateForm): VideoParams {
  const terms = form.video_terms
    .split(/[,，\n]/)
    .map((t) => t.trim())
    .filter(Boolean)

  const params: VideoParams = {
    video_subject: form.video_subject.trim(),
    video_script: form.video_script.trim(),
    video_terms: terms.length ? terms : null,
    video_aspect: form.video_aspect,
    video_concat_mode: form.storyboard_align ? 'sequential' : form.video_concat_mode,
    video_transition_mode:
      form.video_transition === 'none'
        ? null
        : (form.video_transition as VideoParams['video_transition_mode']),
    match_materials_to_script: form.storyboard_align,
    video_clip_duration: form.video_clip_duration,
    video_clip_speed: form.video_clip_speed,
    video_count: form.video_count,
    video_source: form.video_source,
    video_language: form.video_language,
    voice_name: form.voice_name,
    voice_volume: form.voice_volume,
    voice_rate: form.voice_rate,
    bgm_type: form.bgm_type === 'none' ? '' : form.bgm_type,
    bgm_file: form.bgm_file,
    bgm_volume: form.bgm_volume,
    subtitle_enabled: form.subtitle_enabled,
    subtitle_position: form.subtitle_position as VideoParams['subtitle_position'],
    subtitle_display_mode: form.subtitle_display_mode,
    subtitle_animation: form.subtitle_animation,
    custom_position: form.custom_position,
    font_name: form.font_name,
    text_fore_color: form.text_fore_color,
    text_background_color: form.text_background_color,
    rounded_subtitle_background: form.rounded_subtitle_background,
    font_size: form.font_size,
    stroke_color: form.stroke_color,
    stroke_width: form.stroke_width,
    n_threads: form.n_threads,
    paragraph_number: form.paragraph_number,
    video_script_prompt: form.video_script_prompt,
    custom_system_prompt: form.custom_system_prompt,
    // 分镜对齐开关已覆盖上面的 match_materials_to_script，这里不再重复赋值
  }

  if (form.video_source === 'local') {
    params.video_materials = form.local_materials.map((f) => ({
      provider: 'local',
      url: f.file,
    }))
  }
  return params
}
