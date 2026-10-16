import type { ReactNode } from 'react'
import {
  BrainCircuit,
  Images,
  Mic,
  Captions,
  SlidersHorizontal,
} from 'lucide-react'

// 设置页的二级菜单分组：文案 → 素材 → 配音 → 字幕 → 通用。
// 子路由 #/settings/<key> 让刷新/深链停留在当前分组。
// 单独成模块（不放在 Layout/SettingsPage 里）以免组件文件导出非组件值，
// 破坏 React Fast Refresh。
export const SETTINGS_TAB_KEYS = ['llm', 'assets', 'tts', 'subtitle', 'system'] as const
export type SettingsTab = (typeof SETTINGS_TAB_KEYS)[number]

export const SETTINGS_TABS: { key: SettingsTab; label: string; icon: ReactNode }[] = [
  { key: 'llm', label: '文案与脚本模型', icon: <BrainCircuit size={17} /> },
  { key: 'assets', label: '素材来源', icon: <Images size={17} /> },
  { key: 'tts', label: '配音 TTS', icon: <Mic size={17} /> },
  { key: 'subtitle', label: '字幕', icon: <Captions size={17} /> },
  { key: 'system', label: '通用与系统', icon: <SlidersHorizontal size={17} /> },
]

export function settingsTabFromHash(): SettingsTab {
  const seg = location.hash.replace(/^#\/?/, '').split('/')[1] ?? ''
  return (SETTINGS_TAB_KEYS as readonly string[]).includes(seg)
    ? (seg as SettingsTab)
    : 'llm'
}
