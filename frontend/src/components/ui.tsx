import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { Loader2, X, CheckCircle2, AlertCircle } from 'lucide-react'

/* ================= Button ================= */

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

export function Button({
  variant = 'secondary',
  loading = false,
  disabled,
  className = '',
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  loading?: boolean
}) {
  const base =
    'inline-flex items-center justify-center gap-2 rounded-xl font-medium text-sm transition-all duration-150 select-none disabled:opacity-45 disabled:cursor-not-allowed active:scale-[0.98] px-4 h-10'
  const styles: Record<ButtonVariant, string> = {
    primary:
      'text-white shadow-lg shadow-brand/25 bg-gradient-to-r from-brand to-brand2 hover:brightness-110',
    secondary:
      'bg-panel2 border border-line text-ink hover:border-brand/50 hover:text-brand',
    ghost: 'text-muted hover:text-ink hover:bg-panel2',
    danger:
      'bg-danger/10 border border-danger/30 text-danger hover:bg-danger/20',
  }
  return (
    <button
      className={`${base} ${styles[variant]} ${className}`}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <Loader2 size={15} className="animate-spin" />}
      {children}
    </button>
  )
}

/* ================= Row / Section（轻量分区，替代外框） ================= */

export function Row({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div className="flex items-center gap-4 py-1.5">
      <div className="w-28 shrink-0">
        <div className="text-[13px] text-muted">{label}</div>
        {hint && <div className="mt-0.5 text-[11px] text-muted/60">{hint}</div>}
      </div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

export function RowGroup({ children }: { children: ReactNode }) {
  return <div className="divide-y divide-line/40">{children}</div>
}

/* ================= Field ================= */

export function Field({
  label,
  hint,
  children,
  className = '',
}: {
  label: string
  hint?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={className}>
      <div className="mb-1.5 flex items-baseline gap-2">
        <label className="text-sm font-medium text-ink">{label}</label>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

/* ================= Input / Textarea / Select ================= */

const inputCls =
  'w-full rounded-lg bg-panel2/50 border border-transparent px-3.5 text-sm text-ink placeholder:text-muted/60 outline-none transition-colors hover:border-line focus:border-brand/60 focus:bg-panel2 focus:ring-2 focus:ring-brand/10'

export function Input({
  className = '',
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${inputCls} h-10 ${className}`} {...rest} />
}

export function Textarea({
  className = '',
  ...rest
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${inputCls} py-2.5 leading-relaxed ${className}`} {...rest} />
}

export function Select({
  className = '',
  children,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`${inputCls} h-10 pr-8 ${className}`} {...rest}>
      {children}
    </select>
  )
}

/* ================= Segmented ================= */

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  className = '',
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
  className?: string
}) {
  // Chip 风格：无外框容器，选中项渐变胶囊，降低"表单框"密度
  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${className}`}>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-all ${
            value === opt.value
              ? 'bg-gradient-to-r from-brand to-brand2 text-white shadow-sm'
              : 'text-muted hover:bg-panel2 hover:text-ink'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

/* ================= Slider ================= */

export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  format,
  disabled,
}: {
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void
  format?: (v: number) => string
  disabled?: boolean
}) {
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        className="flex-1"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span
        className={`min-w-14 text-right text-sm font-semibold tabular-nums ${
          disabled ? 'text-muted/50' : 'text-gradient'
        }`}
      >
        {format ? format(value) : value}
      </span>
    </div>
  )
}

/* ================= Switch ================= */

export function Switch({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 rounded-full transition-colors disabled:opacity-40 ${
        checked
          ? 'bg-gradient-to-r from-brand to-brand2'
          : 'bg-muted/30 border border-line'
      }`}
    >
      <span
        className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
          checked ? 'left-[22px]' : 'left-0.5'
        }`}
      />
    </button>
  )
}

/* ================= Checkbox ================= */

export function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="inline-flex items-center gap-2 text-sm text-muted hover:text-ink transition-colors"
    >
      <span
        className={`flex h-4.5 w-4.5 items-center justify-center rounded-md border transition-all ${
          checked
            ? 'bg-gradient-to-r from-brand to-brand2 border-transparent'
            : 'border-line bg-panel2'
        }`}
      >
        {checked && <CheckCircle2 size={12} className="text-white" />}
      </span>
      {label}
    </button>
  )
}

/* ================= ColorInput ================= */

export function ColorInput({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <input
        type="color"
        className="h-9 w-12"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <Input
        className="h-9 flex-1 font-mono text-xs"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

/* ================= Card ================= */

export function Card({
  title,
  icon,
  extra,
  children,
  className = '',
}: {
  title?: string
  icon?: ReactNode
  extra?: ReactNode
  children: ReactNode
  className?: string
}) {
  // 轻量化：默认不带边框和背景，用分区标题+细分隔线组织内容，
  // 避免"仪表盘套框"的观感；需要实体卡片的地方自行加 boxed 类。
  if (!title) {
    return <section className={className}>{children}</section>
  }
  return (
    <section className={className}>
      <header className="mb-3 flex items-center gap-2">
        {icon && <span className="text-brand">{icon}</span>}
        <h2 className="text-[13px] font-semibold tracking-wide">{title}</h2>
        <span className="h-px flex-1 bg-line/70" />
        {extra && <div className="text-xs">{extra}</div>}
      </header>
      {children}
    </section>
  )
}

/* ================= Badge ================= */

export function Badge({
  tone = 'neutral',
  children,
}: {
  tone?: 'neutral' | 'success' | 'danger' | 'warn' | 'brand'
  children: ReactNode
}) {
  const tones = {
    neutral: 'bg-muted/15 text-muted border-line',
    success: 'bg-success/10 text-success border-success/30',
    danger: 'bg-danger/10 text-danger border-danger/30',
    warn: 'bg-warn/10 text-warn border-warn/30',
    brand: 'bg-brand/10 text-brand border-brand/30',
  }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

/* ================= Progress ================= */

export function Progress({ value }: { value: number }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/20">
      <div
        className="h-full rounded-full bg-gradient-to-r from-brand to-brand2 transition-all duration-500"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  )
}

/* ================= Modal ================= */

export function Modal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-line bg-panel p-5 shadow-2xl animate-fade-up"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-semibold">{title}</h3>
          <button
            onClick={onClose}
            className="text-muted hover:text-ink transition-colors"
          >
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

/* ================= Toast ================= */

interface ToastItem {
  id: number
  kind: 'success' | 'error'
  text: string
}

const ToastContext = createContext<(kind: 'success' | 'error', text: string) => void>(
  () => {},
)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const idRef = useRef(0)

  const push = useCallback((kind: 'success' | 'error', text: string) => {
    const id = ++idRef.current
    setItems((prev) => [...prev, { id, kind, text }])
    setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), 4200)
  }, [])

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-6 left-1/2 z-[100] flex -translate-x-1/2 flex-col items-center gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm shadow-xl backdrop-blur-md animate-fade-up ${
              t.kind === 'success'
                ? 'border-success/30 bg-panel/95 text-success'
                : 'border-danger/30 bg-panel/95 text-danger'
            }`}
          >
            {t.kind === 'success' ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}

/* ================= Spinner ================= */

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-muted">
      <Loader2 size={18} className="animate-spin text-brand" />
      {label && <span className="text-sm">{label}</span>}
    </div>
  )
}
