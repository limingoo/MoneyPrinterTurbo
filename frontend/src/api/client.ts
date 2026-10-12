import type { ApiEnvelope } from './types'

/** API Key（可选）。服务端配置了 app.api_key 时需要提供 */
const API_KEY_STORAGE = 'mpt.api_key'

export function getApiKey(): string {
  return localStorage.getItem(API_KEY_STORAGE) ?? ''
}

export function setApiKey(key: string) {
  if (key) localStorage.setItem(API_KEY_STORAGE, key)
  else localStorage.removeItem(API_KEY_STORAGE)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  const apiKey = getApiKey()
  if (apiKey) headers.set('x-api-key', apiKey)
  if (init?.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  let res: Response
  try {
    res = await fetch(path, { ...init, headers })
  } catch {
    throw new ApiError(0, '无法连接到 API 服务，请确认 API 已启动')
  }

  let payload: ApiEnvelope<T> | null = null
  try {
    payload = (await res.json()) as ApiEnvelope<T>
  } catch {
    /* 非 JSON 响应 */
  }

  if (!res.ok || !payload || payload.status !== 200) {
    const message =
      payload?.message ||
      (res.ok ? '响应格式异常' : `请求失败（HTTP ${res.status}）`)
    throw new ApiError(res.status, message)
  }
  return payload.data
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' })
}

export function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  return request<T>(path, { method: 'POST', body: form })
}

/** 自定义 multipart（如：params JSON + file 的组合表单） */
export function postForm<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, { method: 'POST', body: form })
}
