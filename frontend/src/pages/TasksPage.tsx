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
  Music4,
  Captions,
  Camera,
} from 'lucide-react'
import {
  Button,
  Badge,
  Progress,
  Modal,
  Spinner,
  useToast,
} from '../components/ui'
import { deleteTask, getTasks, getShootNeeds, removeShootNeed, type ShootNeed } from '../api/endpoints'
import { TASK_STATE, type TaskStatus } from '../api/types'
import { usePolling } from '../hooks'

const PAGE_SIZE = 12

function stateBadge(task: TaskStatus) {
  if (task.state === TASK_STATE.COMPLETE)
    return <Badge tone="success">已完成</Badge>
  if (task.state === TASK_STATE.FAILED)
    return <Badge tone="danger">失败{task.failed_stage ? ` · ${task.failed_stage}` : ''}</Badge>
  if (task.state === TASK_STATE.PROCESSING)
    return (
      <Badge tone="brand">
        <span className="h-1.5 w-1.5 rounded-full bg-brand animate-pulse-dot" />
        生成中 {task.progress ?? 0}%
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
  const [refreshing, setRefreshing] = useState(false)
  // 拍摄任务清单：need.task_id → 该任务的素材缺口
  const [needs, setNeeds] = useState<ShootNeed[]>([])
  const [needsFor, setNeedsFor] = useState<TaskStatus | null>(null)

  const fetchNeeds = useCallback(async () => {
    try {
      setNeeds((await getShootNeeds()).needs)
    } catch {
      /* 清单拉取失败不影响任务列表 */
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
  }, [fetchTasks, fetchNeeds])

  const busy = tasks.some(
    (t) => t.state === TASK_STATE.PROCESSING || (t.state !== TASK_STATE.COMPLETE && t.state !== TASK_STATE.FAILED),
  )

  // 有进行中的任务时 3 秒轮询，否则 15 秒
  usePolling(() => fetchTasks(true), 3000, busy)
  usePolling(() => fetchTasks(true), 15000, !busy && tasks.length > 0)
  // 拍摄清单跟着一起轻量刷新（提交新任务 / 素材入库核销都能及时反映）
  usePolling(fetchNeeds, 3000, busy)
  usePolling(fetchNeeds, 20000, !busy)

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
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {tasks.map((task) => (
            <article
              key={task.task_id}
              className="group flex flex-col overflow-hidden rounded-2xl border border-line bg-panel/80 transition-all hover:border-brand/40 hover:shadow-lg hover:shadow-brand/5"
            >
              {/* 视频预览区 */}
              <div className="relative aspect-video bg-black/40">
                {task.state === TASK_STATE.COMPLETE && task.videos?.length ? (
                  <video
                    src={task.videos[0]}
                    controls
                    preload="metadata"
                    className="h-full w-full object-contain"
                  />
                ) : task.state === TASK_STATE.COMPLETE ? (
                  <div className="flex h-full flex-col items-center justify-center gap-2 text-muted">
                    <CheckCircle2 size={32} className="text-success/70" />
                    <span className="text-xs">任务完成（无视频产物）</span>
                  </div>
                ) : task.state === TASK_STATE.FAILED ? (
                  <div className="flex h-full flex-col items-center justify-center gap-1.5 p-4 text-center">
                    <Badge tone="danger">生成失败</Badge>
                    <p className="line-clamp-3 text-xs text-muted">
                      {task.error || '未知错误'}
                    </p>
                  </div>
                ) : (
                  <div className="flex h-full flex-col items-center justify-center gap-3 px-8">
                    <Badge tone="brand">
                      <span className="h-1.5 w-1.5 rounded-full bg-brand animate-pulse-dot" />
                      {task.state === TASK_STATE.PROCESSING
                        ? `生成中 ${task.progress ?? 0}%`
                        : '排队等待中'}
                    </Badge>
                    <Progress value={task.progress ?? 0} />
                  </div>
                )}
              </div>

              {/* 信息区 */}
              <div className="flex flex-1 flex-col gap-2.5 p-4">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="line-clamp-1 text-sm font-semibold">
                    {task.params?.video_subject || '未命名任务'}
                  </h3>
                  {stateBadge(task)}
                </div>

                <div className="flex items-center gap-3 text-xs text-muted">
                  <span className="inline-flex items-center gap-1">
                    <Clock size={12} />
                    {fmtTime(task.created_at) || task.task_id.slice(0, 8)}
                  </span>
                  {task.params?.video_aspect && (
                    <span>{task.params.video_aspect}</span>
                  )}
                  {task.params?.voice_name && task.params.voice_name !== 'no-voice' && (
                    <span className="line-clamp-1">{task.params.voice_name}</span>
                  )}
                </div>

                {task.videos && task.videos.length > 1 && (
                  <p className="text-xs text-muted">
                    共生成 {task.videos.length} 个视频
                  </p>
                )}

                {openNeeds(task.task_id).length > 0 && (
                  <button
                    type="button"
                    onClick={() => setNeedsFor(task)}
                    className="flex items-center gap-1.5 self-start rounded-lg border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-xs font-medium text-warn transition-colors hover:bg-warn/20"
                  >
                    <Camera size={13} />
                    待拍摄 {openNeeds(task.task_id).length} 项 · 查看拍摄简报
                  </button>
                )}

                {/* 配音 / 字幕产物 */}
                {(task.audio_file || task.subtitle_path) && (
                  <div className="space-y-2 rounded-xl border border-line bg-panel2 p-2.5">
                    {task.audio_file && (
                      <audio
                        src={task.audio_file}
                        controls
                        preload="none"
                        className="h-9 w-full"
                      />
                    )}
                    <div className="flex gap-2">
                      {task.audio_file && (
                        <a
                          href={task.audio_file}
                          download
                          className="inline-flex items-center gap-1 text-xs text-muted transition-colors hover:text-brand"
                        >
                          <Music4 size={12} />
                          配音文件
                        </a>
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
                    </div>
                  </div>
                )}

                <div className="mt-auto flex gap-2 pt-1">
                  {task.videos?.map((v, i) => (
                    <a
                      key={v}
                      href={v}
                      download
                      className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg border border-line bg-panel2 text-xs font-medium text-ink transition-colors hover:border-brand/50 hover:text-brand"
                    >
                      <Download size={13} />
                      下载{i > 0 ? ` ${i + 1}` : ''}
                    </a>
                  ))}
                  <Button
                    variant="danger"
                    className="h-8 rounded-lg px-2.5 text-xs"
                    onClick={() => setDeleting(task)}
                  >
                    <Trash2 size={13} />
                  </Button>
                </div>
              </div>
            </article>
          ))}
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
