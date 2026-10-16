import { useEffect, useState } from 'react'
import { Layout, type Page } from './components/Layout'
import { ToastProvider } from './components/ui'
import { ErrorBoundary } from './components/ErrorBoundary'
import { CreatePage } from './pages/CreatePage'
import { TasksPage } from './pages/TasksPage'
import { SettingsPage } from './pages/SettingsPage'
import { MaterialsPage } from './pages/MaterialsPage'

function currentPage(): Page {
  // 只看第一段，允许设置页使用 #/settings/<tab> 这类子路由。
  const root = location.hash.replace(/^#\/?/, '').split('/')[0]
  return root === 'tasks' || root === 'settings' || root === 'materials' ? root : 'create'
}

export default function App() {
  const [page, setPage] = useState<Page>(currentPage)

  useEffect(() => {
    const onHash = () => setPage(currentPage())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const navigate = (p: Page) => {
    location.hash = p === 'create' ? '/' : `/${p}`
    setPage(p)
  }

  return (
    <ErrorBoundary>
      <ToastProvider>
        <Layout page={page} onNavigate={navigate}>
          {page === 'create' && <CreatePage onCreated={() => navigate('tasks')} />}
          {page === 'tasks' && <TasksPage onGoCreate={() => navigate('create')} />}
        {page === 'materials' && <MaterialsPage />}
          {page === 'settings' && (
            <SettingsThemeProvider>
              {(dark, toggle) => <SettingsPage dark={dark} onToggleTheme={toggle} />}
            </SettingsThemeProvider>
          )}
        </Layout>
      </ToastProvider>
    </ErrorBoundary>
  )
}

/** 读取 Layout 持久化的主题设置（Layout 负责写，这里只读） */
import { useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'

function themeStore() {
  let snapshot = localStorage.getItem('mpt.theme') !== 'light'
  const listeners = new Set<() => void>()
  const observer = new MutationObserver(() => {
    const next = document.documentElement.classList.contains('dark')
    if (next !== snapshot) {
      snapshot = next
      listeners.forEach((l) => l())
    }
  })
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class'],
  })
  return {
    subscribe(l: () => void) {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    getSnapshot() {
      return snapshot
    },
  }
}
const store = themeStore()

function SettingsThemeProvider({ children }: { children: (dark: boolean, toggle: () => void) => ReactNode }) {
  const dark = useSyncExternalStore(store.subscribe, store.getSnapshot)
  return <>{children(dark, () => {
    const next = !store.getSnapshot()
    document.documentElement.classList.toggle('dark', next)
    localStorage.setItem('mpt.theme', next ? 'dark' : 'light')
  })}</>
}
