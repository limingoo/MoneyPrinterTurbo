import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  FileText,
  Sparkles,
  Wand2,
  Captions,
  Music4,
  Send,
  Volume2,
  Crop,
  SlidersHorizontal,
  X,
  Upload,
} from 'lucide-react'
import {
  Button,
  Input,
  Textarea,
  Select,
  Segmented,
  Slider,
  Switch,
  Checkbox,
  ColorInput,
  Badge,
  Row,
  RowGroup,
  useToast,
} from '../components/ui'
import {
  getClonedVoices,
  createVideo,
  createVideoWithCustomAudio,
  createAudio,
  createSubtitle,
  generateScript,
  generateTerms,
  getBgmList,
  uploadBgm,
  checkMaterials,
  remixMaterials,
  makeStoryboard,
  addShootNeeds,
  type ClonedVoiceEntry,
  type MaterialCheckResult,
  type StoryboardShot,
} from '../api/endpoints'
import type { AudioOnlyRequest, FileInfo } from '../api/types'
import {
  loadForm,
  saveForm,
  toParams,
  FONT_OPTIONS,
  TRANSITION_OPTIONS,
  VIDEO_SOURCE_OPTIONS,
  type CreateForm,
} from '../store/form'

const VOICE_NONE = 'no-voice'

/** Azure V2 音色需要付费的 Azure Speech Key，免费 Edge-TTS 不支持 */
const isAzureV2Voice = (v: string) => /-V2-/.test(v)
const DEFAULT_EDGE_VOICE = 'zh-CN-XiaoxiaoNeural-Female'

/** 精简中文音色列表：常用音色 + 中文友好名 */
const CHINESE_VOICE_LABELS: Record<string, string> = {
  'zh-CN-XiaoxiaoNeural-Female': '晓晓 · 温柔标准女声',
  'zh-CN-XiaoyiNeural-Female': '晓伊 · 活泼年轻女声',
  'zh-CN-liaoning-XiaobeiNeural-Female': '晓北 · 东北方言女声',
  'zh-CN-shaanxi-XiaoniNeural-Female': '晓妮 · 陕西方言女声',
  'zh-CN-YunxiNeural-Male': '云希 · 阳光青年男声',
  'zh-CN-YunyangNeural-Male': '云扬 · 新闻播音男声',
  'zh-CN-YunjianNeural-Male': '云健 · 激情解说男声',
  'zh-CN-YunxiaNeural-Male': '云夏 · 少年童声',
  'zh-HK-HiuMaanNeural-Female': '曉曼 · 粤语女声',
  'zh-HK-WanLungNeural-Male': '雲龍 · 粤语男声',
  'zh-TW-HsiaoChenNeural-Female': '曉臻 · 台湾腔女声',
  'zh-TW-YunJheNeural-Male': '雲哲 · 台湾腔男声',
}

type ConfigTab = 'visual' | 'voice' | 'subtitle' | 'music' | 'advanced'

export function CreatePage({ onCreated }: { onCreated: () => void }) {
  const toast = useToast()
  const [form, setForm] = useState<CreateForm>(() => loadForm())
  const [submitting, setSubmitting] = useState(false)
  // 提交细分阶段（用于按钮下面的进度提示）：storyboard=LLM 拆分镜（慢） match=库内匹配
  const [submitPhase, setSubmitPhase] = useState<'storyboard' | 'match' | null>(null)
  const [genScript, setGenScript] = useState(false)
  const [genTerms, setGenTerms] = useState(false)
  const [genAudio, setGenAudio] = useState(false)
  const [genSubtitle, setGenSubtitle] = useState(false)
  const [bgmFiles, setBgmFiles] = useState<FileInfo[]>([])
  const [uploadingBgm, setUploadingBgm] = useState(false)
  // 自定义配音文件（File 不可序列化，刻意不进入 localStorage 持久化）
  const [customAudio, setCustomAudio] = useState<File | null>(null)
  // 我的克隆声音（svclone: 前缀，由后端 SiliconFlow TTS 渲染）
  const [clonedVoices, setClonedVoices] = useState<ClonedVoiceEntry[]>([])
  // 配置面板当前页签
  const [configTab, setConfigTab] = useState<ConfigTab>('visual')
  // 素材库来源的模式状态：自动按关键词匹配，无需手动勾选
  const [checkResult, setCheckResult] = useState<MaterialCheckResult | null>(null)
  const [checking, setChecking] = useState(false)
  const [remixing, setRemixing] = useState(false)

  const parseTermList = useCallback(
    () =>
      form.video_terms
        .split(/[,，\n]/)
        .map((t) => t.trim())
        .filter(Boolean),
    [form.video_terms],
  )

  const loadClonedVoices = () => {
    getClonedVoices()
      .then((r) => setClonedVoices(r.voices))
      .catch(() => {})
  }

  const runTermCheck = useCallback(async () => {
    const terms = parseTermList()
    if (terms.length === 0) {
      setCheckResult(null)
      return
    }
    setChecking(true)
    try {
      setCheckResult(await checkMaterials(terms))
    } catch {
      setCheckResult(null)
    } finally {
      setChecking(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parseTermList])

  const patch = (p: Partial<CreateForm>) => setForm((f) => ({ ...f, ...p }))

  useEffect(() => saveForm(form), [form])

  useEffect(() => {
    // 兼容旧存档：Azure V2 音色自动切回免费 Edge 音色
    if (isAzureV2Voice(form.voice_name) && form.voice_name !== VOICE_NONE) {
      setForm((f) => ({ ...f, voice_name: DEFAULT_EDGE_VOICE }))
      toast('error', '之前选择的音色需要 Azure Speech Key，已自动切换为免费音色')
    }
    loadClonedVoices()
    getBgmList()
      .then((r) => setBgmFiles(r.files))
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 素材库来源下，关键词变化后自动预检（500ms 防抖）
  useEffect(() => {
    if (form.video_source !== 'local') return
    const timer = setTimeout(() => void runTermCheck(), 500)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.video_terms, form.video_source])

  /** 音色下拉：中文精简列表 */
  const voiceGroups = useMemo(() => {
    const options = Object.entries(CHINESE_VOICE_LABELS)
      .filter(([value]) => !isAzureV2Voice(value))
      .map(([value, label]) => ({ value, label }))
    return [{ lang: '免费音色', options }]
  }, [])

  const handleUploadBgm = async (file: File) => {
    setUploadingBgm(true)
    try {
      const r = await uploadBgm(file)
      patch({ bgm_type: 'custom', bgm_file: r.file })
      toast('success', '背景音乐上传成功')
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '上传失败')
    } finally {
      setUploadingBgm(false)
    }
  }

  const handleGenScript = async () => {
    if (!form.video_subject.trim()) {
      toast('error', '请先填写视频主题')
      return
    }
    setGenScript(true)
    try {
      const r = await generateScript({
        video_subject: form.video_subject.trim(),
        paragraph_number: form.paragraph_number,
        video_script_prompt: form.video_script_prompt,
        custom_system_prompt: form.custom_system_prompt,
      })
      patch({ video_script: r.video_script })
      toast('success', '文案生成成功')
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '文案生成失败')
    } finally {
      setGenScript(false)
    }
  }

  const handleGenTerms = async () => {
    if (!form.video_subject.trim()) {
      toast('error', '请先填写视频主题')
      return
    }
    setGenTerms(true)
    try {
      const r = await generateTerms({
        video_subject: form.video_subject.trim(),
        video_script: form.video_script.trim(),
        amount: 5,
        match_materials_to_script: form.match_materials_to_script,
      })
      patch({ video_terms: r.video_terms.join(', ') })
      toast('success', '关键词生成成功')
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '关键词生成失败')
    } finally {
      setGenTerms(false)
    }
  }

  /** 音频/字幕单独生成共用的基础参数 */
  const audioParams = (): AudioOnlyRequest => ({
    video_script: form.video_script.trim(),
    video_language: form.video_language,
    voice_name: form.voice_name,
    voice_volume: form.voice_volume,
    voice_rate: form.voice_rate,
    bgm_type: form.bgm_type === 'none' ? '' : form.bgm_type,
    bgm_file: form.bgm_file,
    bgm_volume: form.bgm_volume,
  })

  const handleAudioOnly = async () => {
    if (!form.video_script.trim()) {
      toast('error', '请先填写或生成文案内容')
      return
    }
    setGenAudio(true)
    try {
      await createAudio(audioParams())
      toast('success', '配音任务已提交')
      onCreated()
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '提交失败')
    } finally {
      setGenAudio(false)
    }
  }

  const handleSubtitleOnly = async () => {
    if (!form.video_script.trim()) {
      toast('error', '请先填写或生成文案内容')
      return
    }
    setGenSubtitle(true)
    try {
      await createSubtitle({
        ...audioParams(),
        subtitle_position: form.subtitle_position,
        subtitle_display_mode: form.subtitle_display_mode,
        subtitle_animation: form.subtitle_animation,
        font_name: form.font_name,
        text_fore_color: form.text_fore_color,
        text_background_color: form.text_background_color,
        rounded_subtitle_background: form.rounded_subtitle_background,
        font_size: form.font_size,
        stroke_color: form.stroke_color,
        stroke_width: form.stroke_width,
      })
      toast('success', '字幕任务已提交')
      onCreated()
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '提交失败')
    } finally {
      setGenSubtitle(false)
    }
  }

  const stopSubmit = () => {
    setSubmitPhase(null)
    setSubmitting(false)
  }

  const handleSubmit = async () => {
    if (!form.video_subject.trim() && !form.video_script.trim()) {
      toast('error', '请填写视频主题或文案')
      return
    }
    if (form.bgm_type === 'custom' && !form.bgm_file) {
      toast('error', '请先上传自定义背景音乐')
      return
    }

    // 素材来源：分镜对齐 / 关键词匹配 / 全库混剪
    // 分镜路径中未被库内任何标签命中的镜头 → 登记为拍摄任务（跟着任务走）
    let missingShots: StoryboardShot[] = []

    let formToSubmit: CreateForm = form
    if (form.video_source === 'local') {
      setSubmitting(true)
      try {
        // 分镜拆分失败 = 停止提交并明确报错（保持开关语义：要拆分镜就按分镜来，不静默换方式）
        let alignShots: StoryboardShot[] | null = null
        if (form.storyboard_align) {
          setSubmitPhase('storyboard')
          try {
            alignShots = (
              await makeStoryboard({
                video_subject: form.video_subject.trim(),
                video_script: form.video_script.trim(),
                video_language: form.video_language,
              })
            ).shots
          } catch {
            stopSubmit()
            toast(
              'error',
              '分镜拆分失败（LLM 响应超时或配置问题）。已停止提交：请稍后重试，或在「画面」页签关闭分镜对齐后再生成。',
            )
            return
          }
        }
        setSubmitPhase('match')
        if (alignShots) {
          // ===== 分镜对齐：逐镜头匹配库内标签 → 顺序排列 =====
          const terms = [...new Set(alignShots.map((s) => s.term))]
          const match = terms.length ? await checkMaterials(terms) : null
          const pool: Record<string, string[]> = match?.matched ?? {}
          if (Object.keys(pool).length === 0) {
            toast(
              'error',
              '素材库里没有匹配任何分镜的素材。请到「素材库」页补充，或关闭分镜对齐改用网络来源。',
            )
            stopSubmit()
            return
          }
          missingShots = alignShots.filter((s) => !(pool[s.term]?.length > 0))
          const used = new Set<string>()
          const ordered: { name: string; file: string; size: number }[] = []
          for (const shot of alignShots) {
            const candidates = pool[shot.term] ?? []
            let chosen = candidates.find((f) => !used.has(f))
            if (!chosen) {
              // 镜头多于素材时循环复用（保留重复镜头以铺满时长）
              chosen = candidates[0]
            }
            if (chosen) {
              ordered.push({ name: chosen, file: chosen, size: 0 })
              used.add(chosen)
            }
          }
          formToSubmit = {
            ...form,
            local_materials: ordered,
            video_concat_mode: 'sequential',
          }
        } else {
          // ===== 普通模式：关键词匹配（或全库混剪） =====
          const terms = parseTermList()
          const result = terms.length ? await checkMaterials(terms) : null
          const files = new Set<string>()
          if (result) {
            for (const matched of Object.values(result.matched)) {
              matched.forEach((f) => files.add(f))
            }
          }
          if (files.size === 0) {
            toast(
              'error',
              terms.length
                ? '素材库里没有与关键词匹配的素材。请到「素材库」页补充，或更换素材来源。'
                : '素材库里还没有素材。请先到「素材库」页上传，或更换素材来源。',
            )
            stopSubmit()
            return
          }
          formToSubmit = {
            ...form,
            local_materials: [...files].slice(0, 12).map((file) => ({
              name: file,
              file,
              size: 0,
            })),
          }
        }
      } catch (e) {
        toast(
          'error',
          e instanceof Error ? e.message : '素材准备失败，请重试或更换素材来源',
        )
        stopSubmit()
        return
      }
    }

    let taskId: string | undefined
    setSubmitPhase(null)
    if (customAudio) {
      taskId = (await createVideoWithCustomAudio(toParams(formToSubmit), customAudio)).task_id
      toast('success', '任务已提交（使用自定义配音），正在生成…')
    } else {
      taskId = (await createVideo(toParams(formToSubmit))).task_id
      toast('success', '任务已提交，正在生成…')
    }

    // 缺口镜头登记为拍摄任务清单（任务页卡片可见，素材上传后自动核销）
    if (missingShots.length > 0 && taskId) {
      try {
        const toBrief = (s: StoryboardShot) => ({
          term: s.term,
          title: s.title || s.term,
          brief: s.brief || '',
        })
        const added = (await addShootNeeds(taskId, missingShots.map(toBrief))).added
        if (added > 0) {
          toast(
            'info',
            `有 ${added} 个镜头素材库里没有，已生成拍摄简报 → 到「任务」页查看该片的拍摄清单。本次先用近似画面补位。`,
          )
        }
      } catch {
        /* 清单登记失败不影响任务 */
      }
    }
    // 成功提交后立即复位按钮（此前成功路径漏掉复位导致按钮永久转圈）
    stopSubmit()
    onCreated()
  }

  const CONFIG_TABS: { key: ConfigTab; label: string; icon: React.ReactElement }[] = [
    { key: 'visual', label: '画面', icon: <Crop size={14} /> },
    { key: 'voice', label: '声音', icon: <Volume2 size={14} /> },
    { key: 'subtitle', label: '字幕', icon: <Captions size={14} /> },
    { key: 'music', label: '音乐', icon: <Music4 size={14} /> },
    { key: 'advanced', label: '高级', icon: <SlidersHorizontal size={14} /> },
  ]

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col lg:overflow-hidden max-lg:pb-8">
      {/* ===== 第一行：主题 + 提交 + 轻量试错 ===== */}
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <Input
          placeholder="你想创作什么视频？输入主题，如「春天的花海」…"
          value={form.video_subject}
          onChange={(e) => patch({ video_subject: e.target.value })}
          className="h-11 min-w-0 flex-1 border-transparent bg-transparent px-1 text-base font-medium"
        />
        <Button
          variant="primary"
          className="h-11 shrink-0 px-6"
          loading={submitting}
          onClick={handleSubmit}
        >
          <Send size={16} />
          生成视频
        </Button>
        {submitting && (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted">
            {submitPhase === 'storyboard'
              ? '正在拆分镜…（免费 LLM 最慢约 2 分钟，请稍候）'
              : submitPhase === 'match'
                ? '正在匹配素材库画面…'
                : '正在提交任务…'}
          </span>
        )}
        <Button
          variant="ghost"
          className="h-11 shrink-0 rounded-full px-3 text-xs"
          loading={genAudio}
          onClick={handleAudioOnly}
          title="需先有文案"
        >
          <Volume2 size={13} />
          仅配音
        </Button>
        <Button
          variant="ghost"
          className="h-11 shrink-0 rounded-full px-3 text-xs"
          loading={genSubtitle}
          onClick={handleSubtitleOnly}
          title="需先有文案"
        >
          <Captions size={13} />
          仅字幕
        </Button>
      </div>

      {/* ===== 主体：左=文案工作区 / 右=配置面板 ===== */}
      <div className="mt-2 grid min-h-0 flex-1 gap-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        {/* ---------- 左：文案工作区 ---------- */}
        <div className="flex min-h-0 flex-col">
          <div className="mb-1.5 flex items-center gap-2">
            <FileText size={15} className="text-brand" />
            <span className="text-[13px] font-semibold tracking-wide">文案</span>
            <span className="h-px flex-1 bg-line/70" />
            <Button
              variant="secondary"
              className="h-7 rounded-full px-3 text-xs"
              loading={genTerms}
              onClick={handleGenTerms}
            >
              <Wand2 size={12} className="text-brand2" />
              AI 关键词
            </Button>
          </div>
          <Textarea
            placeholder="在此输入，或点右上角用 AI 为你生成视频文案…"
            value={form.video_script}
            onChange={(e) => patch({ video_script: e.target.value })}
            className="min-h-0 flex-1 resize-none rounded-xl border border-line/60 bg-panel2/30 text-[15px] leading-relaxed focus:border-brand/50"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              className="h-8 rounded-full px-4 text-xs"
              loading={genScript}
              onClick={handleGenScript}
            >
              <Sparkles size={13} className="text-brand2" />
              AI 生成文案
            </Button>
            <Input
              placeholder="素材关键词（逗号分隔，留空自动生成）"
              value={form.video_terms}
              onChange={(e) => patch({ video_terms: e.target.value })}
              className="h-8 min-w-0 flex-1 rounded-full text-xs"
            />
          </div>
        </div>

        {/* ---------- 右：配置面板（页签式，单屏不滚） ---------- */}
        <div className="flex min-h-0 flex-col rounded-2xl border border-line/70 bg-panel/40 p-3">
          <div className="mb-2 flex flex-wrap items-center gap-1">
            {CONFIG_TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setConfigTab(t.key)}
                className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] font-medium transition-colors ${
                  configTab === t.key
                    ? 'bg-gradient-to-r from-brand to-brand2 text-white'
                    : 'text-muted hover:bg-panel2 hover:text-ink'
                }`}
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <RowGroup>
              {/* ===== 画面 ===== */}
              {configTab === 'visual' && (
                <>
                  <Row label="素材来源">
                    <Segmented
                      value={form.video_source}
                      options={VIDEO_SOURCE_OPTIONS}
                      onChange={(v) => patch({ video_source: v })}
                    />
                    {form.video_source === 'local' && (
                      <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
                        {checking ? (
                          '正在检查素材库…'
                        ) : checkResult ? (
                          <>
                            可覆盖 {checkResult.covered.length} 项
                            {checkResult.missing.length > 0 && (
                              <span className="text-warn">
                                {' '}· 缺 {checkResult.missing.length} 项将自动补位
                              </span>
                            )}
                            ，生成时自动挑选
                            {checkResult.missing.length > 0 && (
                              <>
                                {' ；'}
                                <button
                                  type="button"
                                  className="text-brand underline decoration-dotted disabled:opacity-50"
                                  disabled={remixing}
                                  onClick={() => {
                                    setRemixing(true)
                                    remixMaterials(checkResult.missing, form.video_aspect)
                                      .then((r) => {
                                        if (r.created.length) {
                                          toast(
                                            'success',
                                            `已从库内素材衍生 ${r.created.length} 段新画面（零 AI 费用）`,
                                          )
                                          return void runTermCheck()
                                        }
                                        toast('error', r.reason ?? '衍生失败：库内暂无可用的源素材')
                                      })
                                      .catch((e) =>
                                        toast('error', e instanceof Error ? e.message : '衍生失败'),
                                      )
                                      .finally(() => setRemixing(false))
                                  }}
                                >
                                  {remixing ? '衍生中…' : '先用库内近似画面补齐'}
                                </button>
                              </>
                            )}
                          </>
                        ) : (
                          '生成时按关键词自动挑选'
                        )}
                      </p>
                    )}
                  </Row>
                  <Row label="分镜对齐">
                    <div className="flex items-center gap-3">
                      <Switch
                        checked={form.storyboard_align}
                        onChange={(v) => patch({ storyboard_align: v })}
                      />
                      <span className="text-[11px] leading-snug text-muted">
                        开启后按文案顺序逐句配画面
                        {form.video_source === 'local' ? '（LLM 拆分镜 + 库内标签匹配）' : '（逐句匹配素材顺序）'}
                      </span>
                    </div>
                  </Row>
                  <Row label="画面比例">
                    <Segmented
                      value={form.video_aspect}
                      options={[
                        { value: '9:16', label: '竖屏 9:16' },
                        { value: '16:9', label: '横屏 16:9' },
                        { value: '1:1', label: '方形 1:1' },
                      ]}
                      onChange={(v) => patch({ video_aspect: v })}
                    />
                  </Row>
                  <Row label="片段时长" hint="每段截取秒数">
                    <Slider
                      value={form.video_clip_duration}
                      min={1}
                      max={15}
                      onChange={(v) => patch({ video_clip_duration: v })}
                      format={(v) => `${v}s`}
                    />
                  </Row>
                  <Row label="编排 / 转场">
                    <div className="flex items-center gap-2">
                      <Select
                        value={form.video_concat_mode}
                        onChange={(e) => patch({ video_concat_mode: e.target.value as CreateForm['video_concat_mode'] })}
                        className="min-w-0 flex-1"
                      >
                        <option value="random">随机编排</option>
                        <option value="sequential">顺序播放</option>
                      </Select>
                      <Select
                        value={form.video_transition}
                        onChange={(e) => patch({ video_transition: e.target.value })}
                        className="min-w-0 flex-1"
                      >
                        {TRANSITION_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </Select>
                    </div>
                  </Row>
                  <Row label="数量 / 倍速">
                    <div className="flex items-center gap-3">
                      <span className="shrink-0 text-xs text-muted">×{form.video_count}</span>
                      <div className="min-w-0 flex-1">
                        <Slider
                          value={form.video_count}
                          min={1}
                          max={5}
                          onChange={(v) => patch({ video_count: v })}
                        />
                      </div>
                      <div className="min-w-0 flex-1">
                        <Slider
                          value={form.video_clip_speed}
                          min={0.5}
                          max={2}
                          step={0.1}
                          onChange={(v) => patch({ video_clip_speed: v })}
                          format={(v) => `${v.toFixed(1)}x`}
                        />
                      </div>
                    </div>
                  </Row>
                </>
              )}

              {/* ===== 声音 ===== */}
              {configTab === 'voice' && (
                <>
                  <Row label="音色">
                    <Select
                      value={form.voice_name}
                      onChange={(e) => patch({ voice_name: e.target.value })}
                    >
                      <option value={VOICE_NONE}>无配音</option>
                      <optgroup label="我的克隆声音">
                        {clonedVoices.length === 0 ? (
                          <option value="" disabled>
                            （暂无，去设置页制作）
                          </option>
                        ) : (
                          clonedVoices.map((c) => (
                            <option key={c.id} value={c.voice_name}>
                              {c.label}
                            </option>
                          ))
                        )}
                      </optgroup>
                      {voiceGroups.map((g) => (
                        <optgroup key={g.lang} label={g.lang}>
                          {g.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </optgroup>
                      ))}
                    </Select>
                  </Row>
                  <Row label="自定义配音" hint="上传后跳过 TTS">
                    <div className="flex items-center gap-2">
                      <label className="flex-1">
                        <input
                          type="file"
                          accept="audio/*"
                          className="hidden"
                          onChange={(e) => {
                            const f = e.target.files?.[0] ?? null
                            setCustomAudio(f)
                            e.target.value = ''
                          }}
                        />
                        <span
                          className={`inline-flex h-10 w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-dashed px-3 text-xs transition-colors ${
                            customAudio
                              ? 'border-success/50 text-success'
                              : 'border-line text-muted hover:border-brand/50 hover:text-brand'
                          }`}
                        >
                          <Upload size={13} />
                          {customAudio ? customAudio.name.slice(0, 18) : '选择音频替代'}
                        </span>
                      </label>
                      {customAudio && (
                        <button
                          type="button"
                          className="text-muted hover:text-danger"
                          onClick={() => setCustomAudio(null)}
                        >
                          <X size={15} />
                        </button>
                      )}
                    </div>
                  </Row>
                  <Row label="音量 / 语速">
                    <div className="flex items-center gap-3">
                      <div className="min-w-0 flex-1">
                        <Slider
                          value={form.voice_volume}
                          min={0}
                          max={2}
                          step={0.1}
                          disabled={form.voice_name === VOICE_NONE || !!customAudio}
                          onChange={(v) => patch({ voice_volume: v })}
                          format={(v) => `${Math.round(v * 100)}%`}
                        />
                      </div>
                      <div className="min-w-0 flex-1">
                        <Slider
                          value={form.voice_rate}
                          min={0.5}
                          max={2.5}
                          step={0.1}
                          disabled={form.voice_name === VOICE_NONE || !!customAudio}
                          onChange={(v) => patch({ voice_rate: v })}
                          format={(v) => `${v.toFixed(1)}x`}
                        />
                      </div>
                    </div>
                  </Row>
                </>
              )}

              {/* ===== 字幕 ===== */}
              {configTab === 'subtitle' && (
                <>
                  <Row label="启用字幕">
                    <div className="flex items-center gap-3">
                      <Switch
                        checked={form.subtitle_enabled}
                        onChange={(v) => patch({ subtitle_enabled: v })}
                      />
                      <Segmented
                        className="flex-1"
                        value={form.subtitle_display_mode}
                        options={[
                          { value: 'sentence', label: '整句' },
                          { value: 'word_by_word', label: '逐词' },
                        ]}
                        onChange={(v) => patch({ subtitle_display_mode: v })}
                      />
                    </div>
                  </Row>
                  {form.subtitle_enabled && (
                    <>
                      <Row label="位置 / 动画">
                        <div className="flex items-center gap-2">
                          <Select
                            value={form.subtitle_position}
                            onChange={(e) => patch({ subtitle_position: e.target.value })}
                            className="min-w-0 flex-1"
                          >
                            <option value="bottom">底部</option>
                            <option value="center">居中</option>
                            <option value="top">顶部</option>
                            <option value="two_thirds_bottom">三分之二处</option>
                            <option value="custom">自定义</option>
                          </Select>
                          <Select
                            value={form.subtitle_animation}
                            onChange={(e) =>
                              patch({ subtitle_animation: e.target.value as CreateForm['subtitle_animation'] })
                            }
                            className="min-w-0 flex-1"
                          >
                            <option value="none">无动画</option>
                            <option value="pop_spring">弹出动画</option>
                          </Select>
                        </div>
                      </Row>
                      {form.subtitle_position === 'custom' && (
                        <Row label="位置微调">
                          <Slider
                            value={form.custom_position}
                            min={0}
                            max={100}
                            onChange={(v) => patch({ custom_position: v })}
                            format={(v) => `${v}%`}
                          />
                        </Row>
                      )}
                      <Row label="字体 / 字号">
                        <div className="flex items-center gap-2">
                          <Select
                            value={form.font_name}
                            onChange={(e) => patch({ font_name: e.target.value })}
                            className="min-w-0 flex-1"
                          >
                            {FONT_OPTIONS.map((f) => (
                              <option key={f} value={f}>
                                {f.replace(/\.(ttc|ttf)$/, '')}
                              </option>
                            ))}
                          </Select>
                          <div className="w-24 shrink-0">
                            <Slider
                              value={form.font_size}
                              min={20}
                              max={120}
                              onChange={(v) => patch({ font_size: v })}
                            />
                          </div>
                        </div>
                      </Row>
                      <Row label="文字 / 描边色">
                        <div className="flex items-center gap-2">
                          <ColorInput
                            value={form.text_fore_color}
                            onChange={(v) => patch({ text_fore_color: v })}
                          />
                          <ColorInput
                            value={form.stroke_color}
                            onChange={(v) => patch({ stroke_color: v })}
                          />
                        </div>
                      </Row>
                      <Row label="描边 / 背景">
                        <div className="flex items-center gap-3">
                          <div className="w-24 shrink-0">
                            <Slider
                              value={form.stroke_width}
                              min={0}
                              max={6}
                              step={0.5}
                              onChange={(v) => patch({ stroke_width: v })}
                            />
                          </div>
                          <Checkbox
                            checked={form.text_background_color}
                            onChange={(v) => patch({ text_background_color: v })}
                            label="背景"
                          />
                          <Checkbox
                            checked={form.rounded_subtitle_background}
                            onChange={(v) => patch({ rounded_subtitle_background: v })}
                            label="圆角"
                          />
                        </div>
                      </Row>
                    </>
                  )}
                </>
              )}

              {/* ===== 音乐 ===== */}
              {configTab === 'music' && (
                <>
                  <Row label="音乐来源">
                    <Segmented
                      value={form.bgm_type}
                      options={[
                        { value: 'none', label: '无' },
                        { value: 'random', label: '随机' },
                        { value: 'preset', label: '曲库' },
                        { value: 'custom', label: '上传' },
                      ]}
                      onChange={(v) => patch({ bgm_type: v, bgm_file: '' })}
                    />
                  </Row>
                  {form.bgm_type === 'preset' && (
                    <Row label="曲目">
                      <Select
                        value={form.bgm_file}
                        onChange={(e) => patch({ bgm_file: e.target.value })}
                      >
                        <option value="">请选择…</option>
                        {bgmFiles.map((f) => (
                          <option key={f.file} value={f.file}>
                            {f.name}
                          </option>
                        ))}
                      </Select>
                    </Row>
                  )}
                  {form.bgm_type === 'custom' && (
                    <Row label="上传音乐" hint="mp3/wav ≤30MB">
                      <div className="flex items-center gap-2">
                        <label className="flex-1">
                          <input
                            type="file"
                            accept="audio/*"
                            className="hidden"
                            onChange={(e) => {
                              const f = e.target.files?.[0]
                              if (f) handleUploadBgm(f)
                              e.target.value = ''
                            }}
                          />
                          <span className="inline-flex h-10 w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-dashed border-line px-3 text-xs text-muted transition-colors hover:border-brand/50 hover:text-brand">
                            <Upload size={13} />
                            {uploadingBgm ? '上传中…' : form.bgm_file || '选择文件'}
                          </span>
                        </label>
                        {form.bgm_file && <Badge tone="success">已选</Badge>}
                      </div>
                    </Row>
                  )}
                  <Row label="音乐音量">
                    <Slider
                      value={form.bgm_volume}
                      min={0}
                      max={1}
                      step={0.05}
                      disabled={form.bgm_type === 'none'}
                      onChange={(v) => patch({ bgm_volume: v })}
                      format={(v) => `${Math.round(v * 100)}%`}
                    />
                  </Row>
                </>
              )}

              {/* ===== 高级 ===== */}
              {configTab === 'advanced' && (
                <>
                  <Row label="文案语言">
                    <Select
                      value={form.video_language}
                      onChange={(e) => patch({ video_language: e.target.value })}
                    >
                      <option value="">自动检测</option>
                      <option value="zh-CN">中文（简体）</option>
                      <option value="en-US">English</option>
                      <option value="ja-JP">日本語</option>
                    </Select>
                  </Row>
                  <Row label="文案段落数" hint="AI 生成时">
                    <Slider
                      value={form.paragraph_number}
                      min={1}
                      max={10}
                      onChange={(v) => patch({ paragraph_number: v })}
                    />
                  </Row>
                  <Row label="合成线程">
                    <Slider
                      value={form.n_threads}
                      min={1}
                      max={16}
                      onChange={(v) => patch({ n_threads: v })}
                    />
                  </Row>
                  <Row label="文案提示词" hint="追加到生成指令">
                    <Textarea
                      rows={2}
                      maxLength={2000}
                      value={form.video_script_prompt}
                      onChange={(e) => patch({ video_script_prompt: e.target.value })}
                      placeholder="如：语气轻松幽默，面向年轻人…"
                    />
                  </Row>
                  <Row label="系统提示词" hint="覆盖默认">
                    <Textarea
                      rows={2}
                      maxLength={8000}
                      value={form.custom_system_prompt}
                      onChange={(e) => patch({ custom_system_prompt: e.target.value })}
                    />
                  </Row>
                  <Row label="素材匹配">
                    <Checkbox
                      checked={form.match_materials_to_script}
                      onChange={(v) => patch({ match_materials_to_script: v })}
                      label="关键词按文案顺序匹配素材"
                    />
                  </Row>
                </>
              )}
            </RowGroup>
          </div>
        </div>
      </div>
    </div>
  )
}
