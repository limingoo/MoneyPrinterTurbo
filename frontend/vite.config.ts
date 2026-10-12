import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 本地 API 服务地址（main.py 启动的 FastAPI）
const API_TARGET = 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 8502,
    strictPort: true,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true, configure: stripOrigin },
      // 任务产物（视频/音频/字幕）由 API 以 /tasks/... URI 返回，需要一并代理
      '/tasks': { target: API_TARGET, changeOrigin: true, configure: stripOrigin },
      '/ping': { target: API_TARGET, changeOrigin: true, configure: stripOrigin },
    },
  },
})

/**
 * 后端有浏览器来源防护：带 Origin 头的 POST 会被当作跨域浏览器请求拒绝。
 * 浏览器发给 8502 的 Origin 经过代理原样转发到 8080，就会触发 403。
 * 服务端代理转发的请求本质上不是浏览器跨域请求，这里去掉 Origin 头，
 * 让后端按非浏览器客户端（同源）处理。
 */
function stripOrigin(proxy: { on(event: 'proxyReq', cb: (req: { removeHeader(name: string): void }) => void): void }) {
  proxy.on('proxyReq', (proxyReq) => {
    proxyReq.removeHeader('origin')
  })
}
