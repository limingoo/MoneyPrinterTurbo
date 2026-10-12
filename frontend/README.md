# MoneyPrinterTurbo 新版 WebUI

React 18 + Vite + TypeScript + Tailwind CSS v4 编写的独立前端，替代 Streamlit 界面。

## 本地开发

```bash
cd frontend
npm install
npm run dev        # http://127.0.0.1:8502（需先启动 API：项目根目录 python main.py）
```

开发服务器会把 `/api`、`/tasks`、`/ping` 代理到本机 8080 端口的 FastAPI，无需 CORS 配置。

一键启动（API + 前端 + 自动开浏览器）：项目根目录双击 `newui.bat`。

## 生产构建

```bash
npm run build      # 产物输出到 dist/
```

把 `dist/` 内容复制到后端 `public/` 目录即可由 FastAPI 在 8080 端口直接托管（同源，无需代理）。

## 与后端的关系

**零后端改动**：前端不依赖任何新增的后端接口，音色目录来自 `frontend/public/voices.json`（静态资源）。
上游音色库更新后同步一次即可：

```bash
cd frontend && npm run sync:voices
```

旧版 Streamlit 界面（`webui.bat`）仍可正常使用，两个前端互不影响。

## 目录结构

```
src/
  api/          # 请求封装 + 与后端 schema 对齐的 TS 类型
  components/   # Layout（侧边栏/主题）、ui.tsx（按钮/表单/弹窗等基础组件）
  pages/        # 创作页 / 任务页 / 设置页
  store/        # 创作表单状态与 localStorage 持久化
  hooks/        # 轮询、本地存储 hooks
```
