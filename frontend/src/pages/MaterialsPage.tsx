import { useCallback, useEffect, useState } from 'react'
import {
  Folder,
  Film,
  RefreshCw,
  Trash2,
  Undo2,
  Upload,
  Camera,
} from 'lucide-react'
import {
  Button,
  Badge,
  Modal,
  Spinner,
} from '../components/ui'
import {
  deleteMaterialFile,
  getMaterialLibrary,
  getMaterialTrash,
  getShootNeeds,
  removeShootNeed,
  materialPreviewUrl,
  materialThumbUrl,
  purgeMaterialFile,
  restoreMaterialFile,
  updateMaterialTags,
  uploadMaterial,
  startMaterialIngest,
  getMaterialIngest,
  type MaterialLibraryEntry,
  type ShootNeed,
  type TrashEntry,
} from '../api/endpoints'
import { useToast } from '../components/ui'

type Filter = 'all' | 'ok' | 'low_quality' | 'trash'

const STATUS_LABEL: Record<string, { label: string; tone: 'neutral' | 'warn' | 'danger' | 'brand' }> = {
  ok: { label: '已入库', tone: 'brand' },
  low_quality: { label: '低质', tone: 'warn' },
  duplicate: { label: '与库内重复', tone: 'danger' },
  irrelevant: { label: '疑似无关', tone: 'danger' },
  reject: { label: '不通过', tone: 'danger' },
}

function fmtSize(bytes: number) {
  return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)}MB` : `${(bytes / 1024).toFixed(0)}KB`
}

export function MaterialsPage() {
  const toast = useToast()
  const [files, setFiles] = useState<MaterialLibraryEntry[]>([])
  const [trash, setTrash] = useState<TrashEntry[]>([])
  const [filter, setFilter] = useState<Filter>('all')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [ingesting, setIngesting] = useState(false)
  const [preview, setPreview] = useState<MaterialLibraryEntry | null>(null)
  const [editingTags, setEditingTags] = useState<string | null>(null)
  const [tagDraft, setTagDraft] = useState('')
  const [confirmPurge, setConfirmPurge] = useState<TrashEntry | null>(null)
  // 拍摄任务：跨任务的素材缺口聚合（执行现场视图），点开看简报
  const [openNeeds, setOpenNeeds] = useState<ShootNeed[]>([])
  const [needsOpen, setNeedsOpen] = useState(false)

  const refresh = useCallback((silent = false) => {
    if (!silent) setRefreshing(true)
    Promise.all([getMaterialLibrary(), getMaterialTrash(), getShootNeeds()])
      .then(([lib, trash, needs]) => {
        setFiles(lib.files)
        setTrash(trash.files)
        setOpenNeeds(needs.needs.filter((n) => n.status === 'open'))
      })
      .catch(() => toast('error', '素材库加载失败'))
      .finally(() => {
        setLoading(false)
        setRefreshing(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => refresh(true), [refresh])

  const visible = files.filter((f) =>
    filter === 'all' ? true : filter === 'low_quality' ? f.status === 'low_quality' : f.status === filter,
  )

  const runIngest = (filename: string) => {
    setIngesting(true)
    startMaterialIngest(filename)
      .then(({ job_id }) => {
        const poll = () => {
          getMaterialIngest(job_id)
            .then((job) => {
              if (job.done) {
                setIngesting(false)
                refresh(true)
                const s = job.summary ?? {}
                toast(
                  'success',
                  `整理完成：${s.accepted ?? 0} 段入库${s.low_quality ? `（${s.low_quality} 低质）` : ''}${
                    s.skipped ? `、${s.skipped} 重复跳过` : ''
                  }${s.rejected_irrelevant ? `、${s.rejected_irrelevant} 疑似无关` : ''}`,
                )
              } else {
                setTimeout(poll, 3000)
              }
            })
            .catch(() => setTimeout(poll, 5000))
        }
        setTimeout(poll, 2000)
      })
      .catch((e) => {
        toast('error', e instanceof Error ? e.message : '入库启动失败')
        setIngesting(false)
      })
  }

  const handleUpload = (file: File) => {
    setUploading(true)
    uploadMaterial(file)
      .then((r) => {
        toast('success', '上传完成，开始智能整理…')
        runIngest(r.file)
      })
      .catch((e) => toast('error', e instanceof Error ? e.message : '上传失败'))
      .finally(() => setUploading(false))
  }

  const saveTags = (filename: string, draft: string) => {
    updateMaterialTags(filename, draft.split(/[,，]/).map((t) => t.trim()).filter(Boolean))
      .then(() => {
        toast('success', '标签已更新')
        setEditingTags(null)
        refresh(true)
      })
      .catch((e) => toast('error', e instanceof Error ? e.message : '保存失败'))
  }

  return (
    <div className="animate-fade-up space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold">
            <Folder size={20} className="text-brand" />
            素材库
          </h1>
          <p className="mt-0.5 text-sm text-muted">
            上传后自动拆条、打标；生成视频时按关键词自动匹配
          </p>
        </div>
        <div className="flex gap-2">
          <label>
            <input
              type="file"
              accept="video/*,image/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) handleUpload(f)
                e.target.value = ''
              }}
            />
            <span
              className={`inline-flex h-10 cursor-pointer items-center gap-2 rounded-xl bg-gradient-to-r from-brand to-brand2 px-5 text-sm font-medium text-white ${
                uploading || ingesting ? 'opacity-60' : ''
              }`}
            >
              <Upload size={15} />
              {uploading ? '上传中…' : ingesting ? '智能整理中…' : '上传素材'}
            </span>
          </label>
          <Button variant="secondary" loading={refreshing} onClick={() => refresh()}>
            <RefreshCw size={15} />
          </Button>
        </div>
      </div>

      {/* 拍摄任务（跨任务聚合的素材缺口） */}
      {openNeeds.length > 0 && (
        <button
          type="button"
          onClick={() => setNeedsOpen(true)}
          className="flex w-full items-center gap-2.5 rounded-xl border border-warn/40 bg-warn/10 px-4 py-2.5 text-left transition-colors hover:bg-warn/20"
        >
          <Camera size={16} className="shrink-0 text-warn" />
          <span className="text-sm font-medium text-warn">
            拍摄任务 {openNeeds.length} 项待补
          </span>
          <span className="truncate text-xs text-muted">
            {openNeeds.slice(0, 3).map((n) => n.title).join('；')}
            {openNeeds.length > 3 ? '…' : ''} —— 点击查看拍摄简报，去拍摄后上传这里自动补齐
          </span>
        </button>
      )}

      {/* 筛选 */}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {(
          [
            ['all', `全部 ${files.length}`],
            ['ok', `优质 ${files.filter((f) => f.status === 'ok').length}`],
            ['low_quality', `低质 ${files.filter((f) => f.status === 'low_quality').length}`],
            ['trash', `隔离区 ${trash.length}`],
          ] as [Filter, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`rounded-full px-4 py-1.5 transition-colors ${
              filter === key
                ? 'bg-gradient-to-r from-brand to-brand2 text-white'
                : 'text-muted hover:bg-panel2 hover:text-ink'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <Spinner label="加载素材库…" />
      ) : filter !== 'trash' ? (
        visible.length === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-line py-16 text-muted">
            <Film size={36} className="opacity-40" />
            <p className="text-sm">暂无素材，点击右上角「上传素材」开始建立你的领域素材库</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {visible.map((f) => (
              <div
                key={f.file}
                className="group overflow-hidden rounded-2xl border border-line bg-panel/70 transition-colors hover:border-brand/40"
              >
                {/* 缩略图 */}
                <div
                  className="relative aspect-video cursor-pointer bg-black/40"
                  onClick={() => setPreview(f)}
                >
                  <img
                    src={materialThumbUrl(f.file)}
                    alt={f.file}
                    loading="lazy"
                    className="h-full w-full object-cover"
                    onError={(e) => {
                      ;(e.target as HTMLImageElement).src =
                        'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg"><text y="50%" x="50%" text-anchor="middle" fill="%23888">无法预览</text></svg>'
                    }}
                  />
                  {f.duration != null && (
                    <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-white">
                      {f.duration}s
                    </span>
                  )}
                  {f.status === 'low_quality' && (
                    <span className="absolute left-1 top-1">
                      <Badge tone="warn">低质</Badge>
                    </span>
                  )}
                </div>
                {/* 元信息 */}
                <div className="space-y-1.5 p-2.5">
                  <div className="truncate text-xs font-medium" title={f.file}>
                    {f.kind === 'video' ? '🎬' : '🖼'} {f.file.length > 24 ? f.file.slice(0, 22) + '…' : f.file}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {(f.tags || []).slice(0, 3).map((t) => (
                      <span key={t} className="rounded bg-panel2 px-1.5 py-0.5 text-[10px] text-muted">
                        {t}
                      </span>
                    ))}
                  </div>
                  <div className="flex items-center justify-between pt-0.5 text-[11px] text-muted">
                    <span>{fmtSize(f.size)}</span>
                    <div className="flex gap-2">
                      <button
                        className="hover:text-brand"
                        title="编辑标签"
                        onClick={() => {
                          setEditingTags(f.file)
                          setTagDraft((f.tags || []).join(', '))
                        }}
                      >
                        ✎ 标签
                      </button>
                      <button
                        className="inline-flex items-center gap-0.5 hover:text-danger"
                        onClick={() => {
                          deleteMaterialFile(f.file)
                            .then(() => {
                              toast('success', '已移入隔离区（可恢复）')
                              refresh(true)
                            })
                            .catch((e) => toast('error', e instanceof Error ? e.message : '删除失败'))
                        }}
                      >
                        <Trash2 size={12} />
                        删除
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )
      ) : (
        /* ===== 隔离区 ===== */
        trash.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line py-14 text-center text-sm text-muted">
            隔离区是空的。被规则拒收（重复/无关/黑屏等）的素材会在这里等你复核
          </div>
        ) : (
          <div className="space-y-3">
            {trash.map((t) => {
              const meta = STATUS_LABEL[t.status] ?? { label: t.status, tone: 'neutral' as const }
              return (
                <div
                  key={t.file}
                  className="flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-panel/70 p-3.5"
                >
                  <Badge tone={meta.tone}>{meta.label}</Badge>
                  <span className="max-w-[320px] truncate text-sm" title={t.file}>
                    {t.file}
                  </span>
                  <span className="flex-1 truncate text-xs text-muted">
                    {t.status_reason || '—'}
                  </span>
                  <div className="flex gap-2">
                    <Button
                      variant="secondary"
                      className="h-8 px-3 text-xs"
                      onClick={() =>
                        restoreMaterialFile(t.file)
                          .then(() => {
                            toast('success', '已恢复入库')
                            refresh(true)
                          })
                          .catch((e) => toast('error', e instanceof Error ? e.message : '恢复失败'))
                      }
                    >
                      <Undo2 size={13} />
                      仍要入库
                    </Button>
                    <Button
                      variant="danger"
                      className="h-8 px-3 text-xs"
                      onClick={() => setConfirmPurge(t)}
                    >
                      <Trash2 size={13} />
                      永久删除
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )
      )}

      {/* 标签编辑 */}
      {editingTags && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => setEditingTags(null)}>
          <div
            className="w-full max-w-md rounded-2xl border border-line bg-panel p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="mb-1 font-semibold">编辑标签</h3>
            <p className="mb-3 text-xs text-muted">
              AI 打的标签不准确？直接改。生成视频时按这些标签匹配素材。
            </p>
            <input
              autoFocus
              className="mb-3 w-full rounded-xl border border-line bg-panel2 px-3 py-2 text-sm outline-none focus:border-brand/60"
              value={tagDraft}
              onChange={(e) => setTagDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && saveTags(editingTags, tagDraft)}
              placeholder="逗号分隔，如：面部按摩, 手法特写, 深层清洁"
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setEditingTags(null)}>
                取消
              </Button>
              <Button variant="primary" onClick={() => saveTags(editingTags, tagDraft)}>
                保存
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* 预览 */}
      <Modal open={!!preview} title={preview?.file ?? ''} onClose={() => setPreview(null)}>
        {preview && (
          <div className="space-y-3">
            {preview.kind === 'video' ? (
              <video
                src={materialPreviewUrl(preview.file)}
                controls
                className="w-full rounded-xl bg-black"
              />
            ) : (
              <img src={materialPreviewUrl(preview.file)} className="w-full rounded-xl" />
            )}
            <div className="flex flex-wrap gap-1.5">
              {(preview.tags || []).map((t) => (
                <Badge key={t}>{t}</Badge>
              ))}
            </div>
            {preview.status_reason && (
              <p className="text-xs text-muted">{preview.status_reason}</p>
            )}
          </div>
        )}
      </Modal>

      {/* 永久删除确认 */}
      <Modal open={!!confirmPurge} title="永久删除" onClose={() => setConfirmPurge(null)}>
        <p className="text-sm text-muted">
          确定要永久删除「{confirmPurge?.file}」吗？此操作不可恢复。
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmPurge(null)}>
            取消
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              if (!confirmPurge) return
              purgeMaterialFile(confirmPurge.file)
                .then(() => {
                  toast('success', '已永久删除')
                  setConfirmPurge(null)
                  refresh(true)
                })
                .catch((e) => toast('error', e instanceof Error ? e.message : '删除失败'))
            }}
          >
            确认删除
          </Button>
        </div>
      </Modal>

      {/* 拍摄任务简报（跨任务聚合，入库自动核销） */}
      <Modal
        open={needsOpen}
        title="拍摄任务"
        onClose={() => setNeedsOpen(false)}
      >
        <p className="text-xs text-muted">
          这些镜头在相关视频里没有找到匹配画面。照简报拍摄或收集素材后上传到本页，入库打标命中即自动补齐对应任务。
        </p>
        <div className="mt-3 max-h-[55vh] space-y-3 overflow-y-auto pr-1">
          {openNeeds.map((n) => (
            <div key={n.id} className="rounded-xl border border-line bg-panel2 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                  <Camera size={14} className="shrink-0 text-warn" />
                  <span>{n.title}</span>
                </div>
                <button
                  type="button"
                  className="shrink-0 text-[11px] text-muted transition-colors hover:text-danger"
                  onClick={() => {
                    removeShootNeed(n.id)
                      .then(() => refresh(true))
                      .catch((e) => toast('error', e instanceof Error ? e.message : '忽略失败'))
                  }}
                >
                  忽略
                </button>
              </div>
              {n.brief && (
                <p className="mt-1.5 text-xs leading-relaxed text-muted">{n.brief}</p>
              )}
              <p className="mt-1.5 text-[11px] text-muted/70">
                画面关键词：{n.term}
                {n.task_id === 'creation' ? ' · 来自创作拦截' : ` · 来自任务 ${n.task_id.slice(0, 8)}`}
              </p>
            </div>
          ))}
          {openNeeds.length === 0 && (
            <p className="py-6 text-center text-sm text-muted">没有待补的拍摄任务</p>
          )}
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="secondary" onClick={() => setNeedsOpen(false)}>
            知道了
          </Button>
        </div>
      </Modal>
    </div>
  )
}
