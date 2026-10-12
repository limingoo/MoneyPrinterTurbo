import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/** 全局错误边界：组件崩溃时给出可读提示，而不是白屏/黑屏 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error('[ErrorBoundary]', error)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
          <h1 className="text-lg font-semibold text-danger">页面出了点问题</h1>
          <pre className="max-w-xl overflow-auto rounded-xl border border-line bg-panel2 p-4 text-left text-xs text-muted">
            {this.state.error.message}
          </pre>
          <button
            onClick={() => {
              this.setState({ error: null })
              location.reload()
            }}
            className="rounded-xl bg-gradient-to-r from-brand to-brand2 px-6 py-2.5 text-sm font-medium text-white shadow-lg shadow-brand/25"
          >
            刷新页面
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
