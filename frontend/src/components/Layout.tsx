import { useEffect, useState, type ReactNode } from 'react'
import {
  Clapperboard,
  Film,
  Settings,
  Bot,
  Sun,
  Moon,
  Circle,
  Folder,
  ChevronRight,
} from 'lucide-react'
import { ping } from '../api/endpoints'
import { SETTINGS_TABS, settingsTabFromHash, type SettingsTab } from './settingsTabs'

export type Page = 'create' | 'tasks' | 'materials' | 'settings'

const NAV_ITEMS: { key: Page; label: string; icon: ReactNode }[] = [
  { key: 'create', label: '创作', icon: <Clapperboard size={19} /> },
  { key: 'tasks', label: '任务', icon: <Film size={19} /> },
  { key: 'materials', label: '素材库', icon: <Folder size={19} /> },
  { key: 'settings', label: '设置', icon: <Settings size={19} /> },
]

export function Layout({
  page,
  onNavigate,
  children,
}: {
  page: Page
  onNavigate: (p: Page) => void
  children: ReactNode
}) {
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem('mpt.theme')
    return saved ? saved === 'dark' : true
  })
  const [apiOnline, setApiOnline] = useState<boolean | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(true)
  const [activeTab, setActiveTab] = useState<SettingsTab>(settingsTabFromHash)

  useEffect(() => {
    const onHash = () => setActiveTab(settingsTabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const handleSettingsNav = () => {
    if (page === 'settings') {
      setSettingsOpen((v) => !v)
    } else {
      setSettingsOpen(true)
      onNavigate('settings')
    }
  }

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('mpt.theme', dark ? 'dark' : 'light')
  }, [dark])

  useEffect(() => {
    let alive = true
    const check = () =>
      ping()
        .then((ok) => alive && setApiOnline(ok))
        .catch(() => alive && setApiOnline(false))
    check()
    const timer = setInterval(check, 15000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  return (
    <div className="flex h-full">
      {/* 侧边栏 */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-panel/60 backdrop-blur-sm max-md:hidden">
        <div className="flex items-center gap-3 px-5 py-5">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand to-brand2 text-white shadow-lg shadow-brand/30">
            <Bot size={22} />
          </div>
          <div>
            <div className="text-sm font-bold leading-tight">
              MoneyPrinter<span className="text-gradient">Turbo</span>
            </div>
            <div className="text-[11px] text-muted">AI 短视频创作台</div>
          </div>
        </div>

        <nav className="mt-2 flex flex-col gap-1 px-3">
          {NAV_ITEMS.map((item) => {
            const isSettings = item.key === 'settings'
            return (
              <div key={item.key}>
                <button
                  onClick={() => (isSettings ? handleSettingsNav() : onNavigate(item.key))}
                  className={`flex w-full items-center gap-3 rounded-xl px-4 py-2.5 text-sm font-medium transition-all ${
                    page === item.key
                      ? 'bg-gradient-to-r from-brand/15 to-brand2/10 text-brand'
                      : 'text-muted hover:bg-panel2 hover:text-ink'
                  }`}
                >
                  {item.icon}
                  {item.label}
                  {isSettings && (
                    <ChevronRight
                      size={15}
                      className={`ml-auto transition-transform ${
                        settingsOpen ? 'rotate-90' : ''
                      }`}
                    />
                  )}
                </button>
                {isSettings && settingsOpen && (
                  <div className="mt-1 flex flex-col gap-0.5 pl-4">
                    {SETTINGS_TABS.map((t) => {
                      const active = page === 'settings' && activeTab === t.key
                      return (
                        <a
                          key={t.key}
                          href={`#/settings/${t.key}`}
                          onClick={() => setSettingsOpen(true)}
                          className={`flex items-center gap-2.5 rounded-lg px-3 py-1.5 text-[13px] transition-colors ${
                            active
                              ? 'bg-panel2 font-medium text-brand'
                              : 'text-muted hover:bg-panel2/70 hover:text-ink'
                          }`}
                        >
                          <span
                            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                              active ? 'bg-brand' : 'bg-line'
                            }`}
                          />
                          <span className="truncate">{t.label}</span>
                        </a>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </nav>

        <div className="mt-auto space-y-3 px-5 pb-5">
          <div className="flex items-center gap-2 text-xs text-muted">
            <Circle
              size={8}
              className={
                apiOnline === null
                  ? 'text-muted'
                  : apiOnline
                    ? 'fill-success text-success'
                    : 'fill-danger text-danger animate-pulse-dot'
              }
            />
            {apiOnline === null
              ? '检查 API 连接…'
              : apiOnline
                ? 'API 已连接 (8080)'
                : 'API 未连接'}
          </div>
          <button
            onClick={() => setDark(!dark)}
            className="flex w-full items-center gap-3 rounded-xl border border-line px-4 py-2.5 text-sm text-muted transition-colors hover:border-brand/40 hover:text-ink"
          >
            {dark ? <Sun size={16} /> : <Moon size={16} />}
            {dark ? '浅色模式' : '深色模式'}
          </button>
        </div>
      </aside>

      {/* 主内容 */}
      <main className="flex-1 overflow-y-auto">
        {/* 移动端顶栏 */}
        <div className="sticky top-0 z-40 flex items-center gap-2 border-b border-line bg-bg/90 px-4 py-3 backdrop-blur-md md:hidden">
          <Bot size={20} className="text-brand" />
          <span className="text-sm font-bold">MoneyPrinterTurbo</span>
          <div className="ml-auto flex gap-1">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.key}
                onClick={() => onNavigate(item.key)}
                className={`rounded-lg p-2 ${
                  page === item.key ? 'text-brand bg-brand/10' : 'text-muted'
                }`}
              >
                {item.icon}
              </button>
            ))}
          </div>
        </div>
        <div className="mx-auto max-w-6xl px-6 py-8 max-md:px-4 max-md:py-5">
          {children}
        </div>
      </main>
    </div>
  )
}
