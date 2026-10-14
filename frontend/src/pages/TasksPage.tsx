import { useCallback, useEffect, useState } from 'react'
import {
  Film,
  Trash2,
  Download,
  RefreshCw,
  Clapperboard,
  ChevronLeft,
  ChevronRight,
  Clock,
  CheckCircle2,
  Check,
  Captions,
  Camera,
  Play,
  X,
} from 'lucide-react'
import {
  Button,
  Badge,
  Modal,
  Spinner,
  useToast,
} from '../components/ui'
import { deleteTask, getTasks, getShootNeeds, getPendingCreations, removeShootNeed, removePendingCreation, type ShootNeed, type PendingCreation } from '../api/endpoints'
import { TASK_STATE, type TaskStatus } from '../api/types'
import { saveForm } from '../store/form'
import { usePolling } from '../hooks'

const PAGE_SIZE = 12

/**
 * 流水线阶段与后端 app/services/task.py 的 PIPELINE_STAGES 同源。
 * 后端每个阶段边界都会写入 stage，所以这里直接读，不靠 progress 刻度反推
 * ——进度刻度一旦调整，UI 不会跟着悄悄漂移。
 */
const PIPELINE_STAGES = [
  { key: 'preflight', label: '环境预检' },
  { key: 'script', label: '生成文案' },
  { key: 'terms', label: '提取关键词' },
  { key: 'audio', label: '生成配音' },
  { key: 'subtitle', label: '生成字幕' },
  { key: 'materials', label: '获取素材' },
  { key: 'video', label: '合成视频' },
] as const

function stageLabel(stage?: string) {
  return PIPELINE_STAGES.find((s) => s.key === stage)?.label
}

/** 失败可能发生在流水线之外（排队、调度），此时 stage 不是词表内的值。 */
function stageIndex(task: TaskStatus) {
  const current = task.stage ?? task.failed_stage
  if (current) {
    const found = PIPELINE_STAGES.findIndex((s) => s.key === current)
    // 词表外的失败（webui_worker / scheduling）没有对应步骤，落在最后一步。
    if (found >= 0) return found
    return PIPELINE_STAGES.length - 1
  }
  // 历史任务没有 stage 字段，退回用进度刻度定位到大致阶段。
  if (task.state === TASK_STATE.COMPLETE) return PIPELINE_STAGES.length
  const p = task.progress ?? 0
  if (p >= 50) return PIPELINE_STAGES.length - 1
  if (p >= 40) return 5
  if (p >= 30) return 4
  if (p >= 20) return 3
  if (p >= 10) return 2
  if (p >= 5) return 1
  return 0
}

/** 收起态用的一条细进度轨：在不展开的情况下也能看出走到第几步。 */
function StageTrack({ task }: { task: TaskStatus }) {
  const idx = stageIndex(task)
  const done = task.state === TASK_STATE.COMPLETE
  const failed = task.state === TASK_STATE.FAILED
  const current = done ? -1 : failed ? -2 : idx

  return (
    <div
      className="flex items-center gap-1"
      title={PIPELINE_STAGES[idx]?.label ?? ''}
    >
      {PIPELINE_STAGES.map((s, i) => (
        <span
          key={s.key}
          className={`h-1 flex-1 rounded-full transition-colors ${
            current === -1 || i < idx
              ? 'bg-success/70'
              : current === -2 && i === idx
                ? 'bg-danger'
                : i === idx
                  ? 'bg-brand'
                  : 'bg-line'
          }`}
        />
      ))}
    </div>
  )
}

function StageSteps({ task }: { task: TaskStatus }) {
  const idx = stageIndex(task)
  const failed = task.state === TASK_STATE.FAILED
  const done = task.state === TASK_STATE.COMPLETE

  return (
    <ol className="space-y-1">
      {PIPELINE_STAGES.map((s, i) => {
        // 未命中的分支（如本地素材源不走 terms）保持中性色，不谎报状态。
        const isPast = done || i < idx
        const isCurrent = !done && i === idx
        const isFailed = failed && i === idx
        return (
          <li
            key={s.key}
            className={`flex items-center gap-2 text-xs transition-colors ${
              isCurrent || isFailed ? 'text-ink' : 'text-muted/70'
            }`}
          >
            <span
              className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border text-[9px] ${
                isFailed
                  ? 'border-danger bg-danger/20 text-danger'
                  : isCurrent
                    ? 'border-brand bg-brand/20 text-brand'
                    : isPast
                      ? 'border-success/60 bg-success/15 text-success'
                      : 'border-line'
              }`}
            >
              {isFailed ? (
                <X size={9} />
              ) : isPast ? (
                <Check size={9} />
              ) : isCurrent ? (
                <span className="h-1.5 w-1.5 rounded-full bg-brand animate-pulse-dot" />
              ) : null}
            </span>
            <span className={isCurrent ? 'font-medium' : ''}>{s.label}</span>
          </li>
        )
      })}
    </ol>
  )
}

function stateBadge(task: TaskStatus) {
  if (task.state === TASK_STATE.COMPLETE)
    return <Badge tone="success">已完成</Badge>
  if (task.state === TASK_STATE.FAILED)
    return (
      <Badge tone="danger">
        失败{stageLabel(task.failed_stage) ? ` · ${stageLabel(task.failed_stage)}` : ''}
      </Badge>
    )
  if (task.state === TASK_STATE.PROCESSING)
    return (
      <Badge tone="brand">
        <span className="h-1.5 w-1.5 rounded-full bg-brand animate-pulse-dot" />
        {stageLabel(task.stage) ?? `生成中 ${task.progress ?? 0}%`}
      </Badge>
    )
  return <Badge tone="warn">排队中</Badge>
}

function fmtTime(ts?: number) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export function TasksPage({ onGoCreate }: { onGoCreate: () => void }) {
  const toast = useToast()
  const [tasks, setTasks] = useState<TaskStatus[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState<TaskStatus | null>(null)
  // 展开详情的任务 id：列表形态下默认收起，点行才展开预览/产物。
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  // 拍摄任务清单：need.task_id → 该任务的素材缺口
  const [needs, setNeeds] = useState<ShootNeed[]>([])
  const [needsFor, setNeedsFor] = useState<TaskStatus | null>(null)
  // 等待素材的挂起创作（素材命中后可一键继续生成）
  const [pendings, setPendings] = useState<PendingCreation[]>([])

  const fetchNeeds = useCallback(async () => {
    try {
      setNeeds((await getShootNeeds()).needs)
    } catch {
      /* 清单拉取失败不影响任务列表 */
    }
  }, [])

  const fetchPendings = useCallback(async () => {
    try {
      setPendings((await getPendingCreations()).pendings)
    } catch {
      /* 挂起列表拉取失败不影响任务列表 */
    }
  }, [])

  const fetchTasks = useCallback(
    async (silent = false) => {
      if (!silent) setRefreshing(true)
      try {
        const data = await getTasks(page, PAGE_SIZE)
        setTasks(data.tasks)
        setTotal(data.total)
      } catch {
        /* 轮询失败静默处理，保留上次数据 */
      } finally {
        setLoading(false)
        setRefreshing(false)
      }
    },
    [page],
  )

  useEffect(() => {
    setLoading(true)
    fetchTasks()
    fetchNeeds()
    fetchPendings()
  }, [fetchTasks, fetchNeeds, fetchPendings])

  const busy = tasks.some(
    (t) => t.state === TASK_STATE.PROCESSING || (t.state !== TASK_STATE.COMPLETE && t.state !== TASK_STATE.FAILED),
  )

  // 有进行中的任务时 3 秒轮询，否则 15 秒
  usePolling(() => fetchTasks(true), 3000, busy)
  usePolling(() => fetchTasks(true), 15000, !busy && tasks.length > 0)
  // 拍摄清单跟着一起轻量刷新（提交新任务 / 素材入库核销都能及时反映）
  usePolling(fetchNeeds, 3000, busy)
  usePolling(fetchNeeds, 20000, !busy)
  // 挂起创作跟随轮询：素材入库命中后会转"就绪"
  usePolling(fetchPendings, 3000, busy)
  usePolling(fetchPendings, 20000, !busy)

  const handleDelete = async () => {
    if (!deleting) return
    try {
      await deleteTask(deleting.task_id)
      toast('success', '任务已删除')
      fetchTasks(true)
      fetchNeeds()
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '删除失败')
    } finally {
      setDeleting(null)
    }
  }

  const openNeeds = (taskId: string) =>
    needs.filter((n) => n.task_id === taskId && n.status === 'open')

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="animate-fade-up space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">任务记录</h1>
          <p className="mt-0.5 text-sm text-muted">
            共 {total} 个任务{busy ? '，有任务正在生成' : ''}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" loading={refreshing} onClick={() => fetchTasks()}>
            <RefreshCw size={15} />
            刷新
          </Button>
          <Button variant="primary" onClick={onGoCreate}>
            <Clapperboard size={15} />
            去创作
          </Button>
        </div>
      </div>

      {/* 等待素材的挂起创作：素材命中后一键继续 */}
      {pendings.length > 0 && (
        <div className="space-y-2">
          {pendings.map((p) => {
            return (
              <div
                key={p.id}
                className="flex flex-wrap items-center gap-3 rounded-2xl border border-warn/40 bg-warn/10 px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-semibold text-ink">
                      {p.subject}
                    </span>
                    {p.ready ? (
                      <Badge tone="success">素材已齐 ✓</Badge>
                    ) : (
                      <Badge tone="warn">
                        等待素材 {p.covered?.length ?? 0}/{p.terms.length} 命中
                      </Badge>
                    )}
                  </div>
                  {(p.missing?.length ?? 0) > 0 && (
                    <p className="mt-1 truncate text-xs text-muted">
                      缺：{p.missing!.slice(0, 4).join('、')}
                      {p.missing!.length > 4 ? '…' : ''} —— 到「素材库」上传对应画面（简报见素材库页）
                    </p>
                  )}
                </div>
                <div className="flex gap-2">
                  {p.ready && (
                    <Button
                      variant="primary"
                      className="h-8 px-3 text-xs"
                      onClick={() => {
                        try {
                          saveForm(JSON.parse(p.snapshot))
                          toast('info', '创作表单已恢复，补选配音后点「生成视频」即可')
                          onGoCreate()
                        } catch {
                          toast('error', '快照恢复失败，请重新创作')
                        }
                      }}
                    >
                      <Play size={13} />
                      继续生成
                    </Button>
                  )}
                  <Button
                    variant="danger"
                    className="h-8 px-2.5 text-xs"
                    onClick={() => {
                      removePendingCreation(p.id)
                        .then(() => {
                          toast('success', '已取消该等待')
                          fetchPendings()
                        })
                        .catch((e) =>
                          toast('error', e instanceof Error ? e.message : '取消失败'),
                        )
                    }}
                  >
                    <X size={13} />
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {loading ? (
        <Spinner label="加载任务中…" />
      ) : tasks.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-line py-20 text-muted">
          <Film size={40} className="opacity-40" />
          <p>还没有任务，去创作第一个 AI 视频吧</p>
          <Button variant="primary" onClick={onGoCreate}>
            开始创作
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => {
            const expanded = expandedId === task.task_id
            const needs = openNeeds(task.task_id)
            const active =
              task.state === TASK_STATE.PROCESSING ||
              (task.state !== TASK_STATE.COMPLETE &&
                task.state !== TASK_STATE.FAILED)
            return (
              <article
                key={task.task_id}
                className="overflow-hidden rounded-xl border border-line bg-panel/70 transition-colors hover:border-brand/40"
              >
                {/* 收起行：缩略图 + 主题/元信息 + 状态 + 操作 */}
                <div className="flex items-center gap-3 p-3">
                  <button
                    type="button"
                    onClick={() =>
                      setExpandedId(expanded ? null : task.task_id)
                    }
                    className="flex min-w-0 flex-1 items-center gap-3 text-left"
                  >
                    <span className="relative h-11 w-[4.5rem] shrink-0 overflow-hidden rounded-lg bg-black/40">
                      {task.state === TASK_STATE.COMPLETE &&
                      task.videos?.length ? (
                        <video
                          src={task.videos[0]}
                          preload="metadata"
                          muted
                          className="h-full w-full object-cover"
                        />
                      ) : task.state === TASK_STATE.FAILED ? (
                        <span className="flex h-full items-center justify-center text-danger/70">
                          <X size={18} />
                        </span>
                      ) : task.state === TASK_STATE.COMPLETE ? (
                        <span className="flex h-full items-center justify-center text-success/70">
                          <CheckCircle2 size={18} />
                        </span>
                      ) : active ? (
                        <span className="flex h-full items-center justify-center">
                          <span className="h-2 w-2 rounded-full bg-brand animate-pulse-dot" />
                        </span>
                      ) : null}
                    </span>

                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-ink">
                          {task.params?.video_subject || '未命名任务'}
                        </span>
                        {stateBadge(task)}
                      </span>
                      <span className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs text-muted">
                        <span className="inline-flex items-center gap-1">
                          <Clock size={11} />
                          {fmtTime(task.created_at) ||
                            task.task_id.slice(0, 8)}
                        </span>
                        {task.params?.video_aspect && (
                          <span>{task.params.video_aspect}</span>
                        )}
                        {task.params?.voice_name &&
                          task.params.voice_name !== 'no-voice' && (
                            <span className="truncate">
                              {task.params.voice_name}
                            </span>
                          )}
                        {task.videos && task.videos.length > 1 && (
                          <span>{task.videos.length} 个视频</span>
                        )}
                      </span>
                      {active && (
                        <span className="mt-1.5 block">
                          <StageTrack task={task} />
                        </span>
                      )}
                    </span>
                  </button>

                  <div className="flex shrink-0 items-center gap-1.5">
                    {needs.length > 0 && (
                      <button
                        type="button"
                        onClick={() => setNeedsFor(task)}
                        title="查看拍摄简报"
                        className="inline-flex h-8 items-center gap-1 rounded-lg border border-warn/40 bg-warn/10 px-2 text-xs font-medium text-warn transition-colors hover:bg-warn/20"
                      >
                        <Camera size={13} />
                        {needs.length}
                      </button>
                    )}
                    {task.videos?.slice(0, 1).map((v) => (
                      <a
                        key={v}
                        href={v}
                        download
                        title="下载视频"
                        className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-panel2 text-ink transition-colors hover:border-brand/50 hover:text-brand"
                      >
                        <Download size={13} />
                      </a>
                    ))}
                    <Button
                      variant="danger"
                      className="h-8 w-8 px-0"
                      title="删除任务"
                      onClick={() => setDeleting(task)}
                    >
                      <Trash2 size={13} />
                    </Button>
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedId(expanded ? null : task.task_id)
                      }
                      title={expanded ? '收起' : '展开详情'}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:text-brand"
                    >
                      <ChevronRight
                        size={15}
                        className={`transition-transform ${expanded ? 'rotate-90' : ''}`}
                      />
                    </button>
                  </div>
                </div>

                {/* 展开区：预览、阶段明细、产物 */}
                {expanded && (
                  <div className="space-y-3 border-t border-line/70 bg-panel2/30 p-3">
                    {task.state === TASK_STATE.COMPLETE &&
                      task.videos?.length ? (
                      <video
                        src={task.videos[0]}
                        controls
                        preload="metadata"
                        className="max-h-[45vh] w-full rounded-lg bg-black/40 object-contain"
                      />
                    ) : null}

                    {task.state === TASK_STATE.FAILED && task.error && (
                      <p className="rounded-lg border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
                        {task.error}
                      </p>
                    )}

                    {active && (
                      <div className="flex gap-4">
                        <div className="w-40 shrink-0">
                          <p className="mb-1.5 text-[11px] font-medium text-muted">
                            流水线阶段
                          </p>
                          <StageSteps task={task} />
                        </div>
                        {task.audio_file && (
                          <div className="min-w-0 flex-1">
                            <p className="mb-1.5 text-[11px] font-medium text-muted">
                              配音
                            </p>
                            <audio
                              src={task.audio_file}
                              controls
                              preload="none"
                              className="h-9 w-full"
                            />
                          </div>
                        )}
                      </div>
                    )}

                    {task.subtitle_path && (
                      <a
                        href={task.subtitle_path}
                        download
                        className="inline-flex items-center gap-1 text-xs text-muted transition-colors hover:text-brand"
                      >
                        <Captions size={12} />
                        字幕文件
                      </a>
                    )}

                    {task.videos && task.videos.length > 0 && (
                      <div className="flex flex-wrap gap-2">
                        {task.videos.map((v, i) => (
                          <a
                            key={v}
                            href={v}
                            download
                            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-panel px-3 text-xs font-medium text-ink transition-colors hover:border-brand/50 hover:text-brand"
                          >
                            <Download size={13} />
                            下载{i > 0 ? ` ${i + 1}` : ''}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </article>
            )
          })}
        </div>
      )}

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <Button
            variant="ghost"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft size={16} />
            上一页
          </Button>
          <span className="text-sm text-muted">
            {page} / {totalPages}
          </span>
          <Button
            variant="ghost"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
            <ChevronRight size={16} />
          </Button>
        </div>
      )}

      {/* 删除确认 */}
      <Modal
        open={!!deleting}
        title="删除任务"
        onClose={() => setDeleting(null)}
      >
        <p className="text-sm text-muted">
          确定删除任务「{deleting?.params?.video_subject || deleting?.task_id}」吗？
          该任务的所有生成文件都会被移除，操作不可恢复。
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setDeleting(null)}>
            取消
          </Button>
          <Button variant="danger" onClick={handleDelete}>
            <Trash2 size={15} />
            确认删除
          </Button>
        </div>
      </Modal>

      {/* 拍摄任务清单（本片画面缺口的拍摄简报） */}
      <Modal
        open={!!needsFor}
        title="本片拍摄清单"
        onClose={() => setNeedsFor(null)}
      >
        <p className="text-xs text-muted">
          这些镜头素材库里没有匹配画面，本次成片用近似画面补位。照简报拍摄或收集后上传到「素材库」，入库打标命中即自动补齐。
        </p>
        <div className="mt-3 max-h-[50vh] space-y-3 overflow-y-auto pr-1">
          {needsFor &&
            openNeeds(needsFor.task_id).map((n) => (
              <div
                key={n.id}
                className="rounded-xl border border-line bg-panel2 p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                    <Camera size={14} className="shrink-0 text-warn" />
                    <span>{n.title}</span>
                  </div>
                  <button
                    type="button"
                    className="shrink-0 text-[11px] text-muted transition-colors hover:text-danger"
                    onClick={() => {
                      removeShootNeed(n.id).then(() => fetchNeeds()).catch(() => {})
                    }}
                  >
                    忽略
                  </button>
                </div>
                {n.brief && (
                  <p className="mt-1.5 text-xs leading-relaxed text-muted">{n.brief}</p>
                )}
                {n.term && (
                  <p className="mt-1.5 text-[11px] text-muted/70">画面关键词：{n.term}</p>
                )}
              </div>
            ))}
          {needsFor && openNeeds(needsFor.task_id).length === 0 && (
            <p className="py-6 text-center text-sm text-muted">清单已全部补齐</p>
          )}
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="secondary" onClick={() => setNeedsFor(null)}>
            知道了
          </Button>
        </div>
      </Modal>
    </div>
  )
}
