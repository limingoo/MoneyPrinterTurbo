import { useEffect, useState } from 'react'

/** 本地存储持久化 state */
export function useLocalStorage<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw === null ? initial : (JSON.parse(raw) as T)
    } catch {
      return initial
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      /* ignore */
    }
  }, [key, value])
  return [value, setValue] as const
}

/** 定时轮询（active=false 或 interval<=0 时暂停） */
export function usePolling(
  fn: () => void,
  intervalMs: number,
  active = true,
) {
  useEffect(() => {
    if (!active || intervalMs <= 0) return
    const timer = setInterval(fn, intervalMs)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, active, fn])
}
