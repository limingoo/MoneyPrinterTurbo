import { useEffect, useState } from 'react'
import {
  Settings as SettingsIcon,
  KeyRound,
  Info,
  Sun,
  Moon,
  BrainCircuit,
  Images,
  PlugZap,
  Captions,
  ChevronRight,
} from 'lucide-react'
import {
  Card,
  Field,
  Input,
  Button,
  Badge,
  Select,
  useToast,
} from '../components/ui'
import { getApiKey, setApiKey } from '../api/client'
import {
  getConfig,
  getProviderModels,
  ping,
  testLLM,
  updateConfig,
  cloneVoice,
  deleteClonedVoice,
  saveSiliconFlowKey,
  type ConfigView,
  type LLMProviderMeta,
  type AIVideoField,
  type AIVideoProviderUpdate,
} from '../api/endpoints'
import { Upload, Mic } from 'lucide-react'
import { SETTINGS_TABS, settingsTabFromHash, type SettingsTab } from '../components/settingsTabs'

const MATERIAL_LABELS: Record<string, string> = {
  pexels_api_keys: 'Pexels API Keys',
  pixabay_api_keys: 'Pixabay API Keys',
  coverr_api_keys: 'Coverr API Keys',
}

const MATERIAL_LINKS: Record<string, string> = {
  pexels_api_keys: 'https://www.pexels.com/api/',
  pixabay_api_keys: 'https://pixabay.com/api/docs/',
  coverr_api_keys: 'https://coverr.co/',
}

// 图库检索源：value 即写入 config 的 video_source 值
const STOCK_SOURCES = [
  { value: 'pexels', key: 'pexels_api_keys', label: 'Pexels' },
  { value: 'pixabay', key: 'pixabay_api_keys', label: 'Pixabay' },
  { value: 'coverr', key: 'coverr_api_keys', label: 'Coverr' },
] as const

// AI 视频供应商的展示顺序与字段标签。字段值来自后端 /config 的 ai_video。
const AI_VIDEO_FIELD_ORDER: Record<string, string[]> = {
  metaso_minimax: ['api_key', 'resolution', 'base_url'],
  ofox: ['api_key', 'model', 'resolution', 'min_duration', 'max_duration', 'provider', 'base_url'],
  volcengine_seedance: ['api_key', 'model', 'resolution', 'min_duration', 'max_duration', 'base_url'],
  wavespeed: ['api_keys', 'model', 'min_duration', 'max_duration'],
  muapi: ['api_key', 'video_endpoint', 'resolution', 'min_duration', 'max_duration', 'base_url'],
}

const AI_VIDEO_FIELD_LABELS: Record<string, string> = {
  api_key: 'API Key',
  api_keys: 'API Keys',
  base_url: 'Base URL',
  model: '模型',
  video_endpoint: '模型端点',
  resolution: '分辨率',
  min_duration: '最短时长（秒）',
  max_duration: '最长时长（秒）',
  provider: '上游供应商',
}

// 高级字段默认折叠，避免主界面过长。
const AI_VIDEO_ADVANCED_FIELDS = new Set(['base_url', 'provider'])

// 需要下拉选择的字段（provider/field → 选项）。
const AI_VIDEO_SELECT_OPTIONS: Record<string, string[]> = {
  'metaso_minimax/resolution': ['768P', '2K'],
  'volcengine_seedance/resolution': ['480p', '720p', '1080p'],
  'ofox/resolution': ['480p', '720p'],
  'ofox/provider': ['byteplus', 'volcengine'],
}

const AI_VIDEO_HELP: Record<string, string> = {
  metaso_minimax: '秘塔代理的 MiniMax H3 文生视频，服务端仅支持 768P / 2K',
  ofox: 'OFox 聚合的 Seedance / Wan 等文生视频模型，单一密钥',
  volcengine_seedance: '火山引擎方舟 Seedance 异步视频生成，按提交片段计费',
  wavespeed: 'WaveSpeed AI 文生视频，按请求计费',
  muapi: 'MuAPI 文生视频端点，模型由「模型端点」字段决定，按请求计费',
}

export function SettingsPage({
  dark,
  onToggleTheme,
}: {
  dark: boolean
  onToggleTheme: () => void
}) {
  const toast = useToast()
  const [apiKey, setKey] = useState(getApiKey())
  const [saved, setSaved] = useState(false)
  const [apiOnline, setApiOnline] = useState<boolean | null>(null)
  const [cfg, setCfg] = useState<ConfigView | null>(null)
  const [llmProvider, setLlmProvider] = useState('')
  const [llmKey, setLlmKey] = useState('')
  const [llmBaseUrl, setLlmBaseUrl] = useState('')
  const [llmModel, setLlmModel] = useState('')
  const [materialKeys, setMaterialKeys] = useState<Record<string, string>>({})
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [modelSource, setModelSource] = useState('')
  // 声音克隆
  const [sfKey, setSfKey] = useState('')
  const [cloneName, setCloneName] = useState('')
  const [cloneText, setCloneText] = useState('')
  const [cloneFile, setCloneFile] = useState<File | null>(null)
  const [cloning, setCloning] = useState(false)
  const [savingSfKey, setSavingSfKey] = useState(false)
  const [clonedList, setClonedList] = useState<ConfigView['cloned_voices']>([])
  const [deleting, setDeleting] = useState<string | null>(null)
  const [savingLLM, setSavingLLM] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null)
  const [savingMaterials, setSavingMaterials] = useState(false)
  // 默认素材源 + AI 视频供应商的可编辑字段
  const [videoSource, setVideoSource] = useState('pexels')
  const [aiVideoEdits, setAiVideoEdits] = useState<Record<string, Record<string, string>>>({})
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null)
  const [savingProvider, setSavingProvider] = useState<string | null>(null)
  const [tab, setTab] = useState<SettingsTab>(settingsTabFromHash)

  const goTab = (key: SettingsTab) => {
    location.hash = `#/settings/${key}`
    setTab(key)
  }

  useEffect(() => {
    const onHash = () => setTab(settingsTabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const loadConfig = () => {
    getConfig()
      .then((c) => {
        setCfg(c)
        setVideoSource(c.materials.video_source || 'pexels')
        setAiVideoEdits({})
        setLlmProvider(c.llm.provider)
        setLlmBaseUrl(c.llm.values.base_url.value)
        setLlmModel(c.llm.values.model_name.value)
        const m: Record<string, string> = {}
        for (const key of Object.keys(MATERIAL_LABELS)) m[key] = ''
        setMaterialKeys(m)
        setClonedList(c.cloned_voices ?? [])
        // 拉取当前 provider 的模型列表（自动发现或静态推荐清单）
        getProviderModels(c.llm.provider)
          .then((r) => {
            setModelOptions(r.models)
            setModelSource(r.source)
          })
          .catch(() => setModelOptions([]))
      })
      .catch(() => toast('error', '配置读取失败'))
  }

  useEffect(() => {
    ping()
      .then((ok) => setApiOnline(ok))
      .catch(() => setApiOnline(false))
    loadConfig()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const spec: LLMProviderMeta['default_model'] extends never ? never : LLMProviderMeta | undefined =
    cfg?.llm.providers.find((p) => p.id === llmProvider)

  const handleProviderChange = (id: string) => {
    setLlmProvider(id)
    const meta = cfg?.llm.providers.find((p) => p.id === id)
    // 切换 Provider 时清空本地的 key/url/model 输入，placeholder 提示默认值
    setLlmKey('')
    setLlmBaseUrl('')
    setLlmModel('')
    if (meta && !cfg?.llm.providers.length) return
    // 同步刷新模型候选列表
    setModelOptions([])
    setModelSource('')
    getProviderModels(id)
      .then((r) => {
        setModelOptions(r.models)
        setModelSource(r.source)
      })
      .catch(() => setModelOptions([]))
  }

  const handleSaveLLM = async () => {
    if (!spec) return
    if (spec.requires_model_name && !llmModel.trim()) {
      toast('error', '请填写模型名称')
      return
    }
    setSavingLLM(true)
    try {
      await updateConfig({
        llm: {
          provider: llmProvider,
          api_key: llmKey || undefined, // 空输入 = 保留现有 Key
          base_url: llmBaseUrl,
          model_name: llmModel,
        },
      })
      toast('success', 'LLM 配置已保存并即时生效')
      loadConfig()
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '保存失败')
    } finally {
      setSavingLLM(false)
    }
  }

  const handleTestLLM = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const r = await testLLM()
      setTestResult(
        r.ok
          ? { ok: true, text: `连接成功，模型返回了关键词：${(r.sample_terms ?? []).join('、')}` }
          : { ok: false, text: r.error ?? '未知错误' },
      )
    } catch (e) {
      setTestResult({ ok: false, text: e instanceof Error ? e.message : '测试失败' })
    } finally {
      setTesting(false)
    }
  }

  const handleSaveMaterials = async () => {
    setSavingMaterials(true)
    try {
      const payload: Record<string, string> = {}
      for (const [key, value] of Object.entries(materialKeys)) {
        if (value.trim() !== '') payload[key] = value
      }
      await updateConfig({ materials: { ...payload, video_source: videoSource } })
      toast('success', '图库配置已保存并即时生效')
      loadConfig()
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '保存失败')
    } finally {
      setSavingMaterials(false)
    }
  }

  const handleSaveProvider = async (pid: string) => {
    if (!cfg) return
    const provider = cfg.ai_video[pid]
    const edits = aiVideoEdits[pid] ?? {}
    const payload: Record<string, string | number> = {}
    for (const name of AI_VIDEO_FIELD_ORDER[pid] ?? []) {
      if (!(name in edits)) continue
      const field = provider.fields[name]
      const raw = edits[name].trim()
      if (field?.kind === 'int') {
        if (raw === '') continue
        payload[name] = Number(raw)
      } else {
        payload[name] = raw
      }
    }
    if (Object.keys(payload).length === 0) {
      toast('error', '没有修改需要保存')
      return
    }
    setSavingProvider(pid)
    try {
      await updateConfig({ ai_video: { [pid]: payload as AIVideoProviderUpdate } })
      toast('success', `${provider.label} 已保存并即时生效`)
      loadConfig()
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '保存失败')
    } finally {
      setSavingProvider(null)
    }
  }

  /** AI 视频供应商单个字段的受控值：密钥类未编辑时留空，其余回退配置/默认值。 */
  const aiFieldValue = (pid: string, name: string, field: AIVideoField) => {
    const edited = aiVideoEdits[pid]?.[name]
    if (edited !== undefined) return edited
    if (field.kind === 'secret' || field.kind === 'secret_list') return ''
    return String(field.value ?? field.default ?? '')
  }

  const aiFieldHint = (field: AIVideoField) => {
    if (field.kind === 'secret_list') {
      return field.count
        ? `已配置 ${field.count} 个（${field.masked}），留空不修改`
        : '未配置；多个 Key 用逗号分隔'
    }
    if (field.kind === 'secret') {
      return field.has_value ? `已配置（${field.masked}），留空不修改` : '未配置'
    }
    return field.default !== undefined && String(field.default) !== ''
      ? `默认：${field.default}`
      : undefined
  }

  // 仅列出已配置密钥、可在创作页直接使用的 AI 视频源
  const enabledAiSources = cfg
    ? Object.entries(cfg.ai_video).filter(([, p]) => p.enabled)
    : []

  const renderAiProvider = (pid: string) => {
    if (!cfg) return null
    const provider = cfg.ai_video[pid]
    if (!provider) return null
    const open = expandedProvider === pid
    const fields = AI_VIDEO_FIELD_ORDER[pid] ?? Object.keys(provider.fields)
    const primary = fields.filter((n) => !AI_VIDEO_ADVANCED_FIELDS.has(n))
    const advanced = fields.filter((n) => AI_VIDEO_ADVANCED_FIELDS.has(n))

    const renderField = (name: string) => {
      const field = provider.fields[name]
      if (!field) return null
      const label = AI_VIDEO_FIELD_LABELS[name] ?? name
      const isSecret = field.kind === 'secret' || field.kind === 'secret_list'
      const selectOptions = AI_VIDEO_SELECT_OPTIONS[`${pid}/${name}`]
      const value = aiFieldValue(pid, name, field)
      const onChange = (v: string) =>
        setAiVideoEdits((prev) => ({ ...prev, [pid]: { ...(prev[pid] ?? {}), [name]: v } }))
      return (
        <Field key={name} label={label} hint={aiFieldHint(field)}>
          {selectOptions ? (
            <Select value={value} onChange={(e) => onChange(e.target.value)}>
              {selectOptions.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
          ) : (
            <Input
              type={isSecret ? 'password' : field.kind === 'int' ? 'number' : 'text'}
              placeholder={
                isSecret
                  ? field.has_value || field.count
                    ? '已保存，留空不修改'
                    : '粘贴密钥'
                  : String(field.default ?? '')
              }
              value={value}
              onChange={(e) => onChange(e.target.value)}
            />
          )}
        </Field>
      )
    }

    return (
      <div key={pid} className="rounded-xl border border-line">
        <button
          type="button"
          onClick={() => setExpandedProvider(open ? null : pid)}
          className="flex w-full items-center gap-2.5 px-4 py-3 text-left transition-colors hover:bg-panel2/60"
        >
          <ChevronRight
            size={15}
            className={`shrink-0 text-muted transition-transform ${open ? 'rotate-90' : ''}`}
          />
          <span className="text-sm font-medium text-ink">{provider.label}</span>
          <Badge tone={provider.enabled ? 'success' : 'neutral'}>
            {provider.enabled ? '已配置' : '未配置'}
          </Badge>
          {AI_VIDEO_HELP[pid] && (
            <span className="ml-auto hidden truncate pl-3 text-[11px] text-muted md:block">
              {AI_VIDEO_HELP[pid]}
            </span>
          )}
        </button>
        {open && (
          <div className="space-y-4 border-t border-line px-4 py-4">
            <div className="grid gap-4 md:grid-cols-3">{primary.map(renderField)}</div>
            {advanced.length > 0 && (
              <details className="rounded-lg border border-line/70 px-3 py-2">
                <summary className="cursor-pointer text-xs text-muted">高级</summary>
                <div className="mt-3 grid gap-4 md:grid-cols-3">{advanced.map(renderField)}</div>
              </details>
            )}
            <div className="flex items-center gap-3">
              <Button
                variant="primary"
                loading={savingProvider === pid}
                onClick={() => void handleSaveProvider(pid)}
              >
                保存
              </Button>
              <span className="text-xs text-muted">保存后对后续生成任务生效</span>
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="animate-fade-up space-y-5">
      <div className="flex items-center gap-3">
        <SettingsIcon size={22} className="text-brand" />
        <h1 className="text-xl font-bold">设置</h1>
      </div>

      {/* 移动端：全局侧边栏隐藏，这里用横向 tab 提供同样的分组切换 */}
      <nav className="flex gap-1 overflow-x-auto pb-1 lg:hidden">
        {SETTINGS_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => goTab(t.key)}
            className={`flex shrink-0 items-center gap-2 rounded-xl px-3.5 py-2 text-sm font-medium transition-all ${
              tab === t.key
                ? 'bg-gradient-to-r from-brand/15 to-brand2/10 text-brand'
                : 'text-muted hover:bg-panel2 hover:text-ink'
            }`}
          >
            {t.icon}
            <span className="whitespace-nowrap">{t.label}</span>
          </button>
        ))}
      </nav>

      <div className="space-y-5">
        {/* ===== 文案与脚本模型 ===== */}
        <Card
          title="AI 模型（LLM）"
          icon={<BrainCircuit size={16} />}
          className={tab === 'llm' ? '' : 'hidden'}
        >
          {!cfg ? (
            <p className="text-sm text-muted">加载中…</p>
          ) : (
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-3">
                <Field label="提供商" hint="切换后字段跟随变化">
                  <Select value={llmProvider} onChange={(e) => handleProviderChange(e.target.value)}>
                    {cfg.llm.providers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field
                  label="模型名称"
                  hint={
                    modelOptions.length
                      ? modelSource === 'api'
                        ? `从该服务商获取到 ${modelOptions.length} 个模型，可搜索`
                        : `推荐模型 ${modelOptions.length} 个`
                      : spec?.default_model
                        ? `官方默认：${spec.default_model}`
                        : undefined
                  }
                >
                  <input
                    list={`models-${llmProvider}`}
                    placeholder={spec?.default_model || '输入或从列表选择模型'}
                    value={llmModel}
                    onChange={(e) => setLlmModel(e.target.value)}
                    className={`w-full h-10 rounded-xl bg-panel2 border border-line px-3.5 text-sm text-ink outline-none transition-colors focus:border-brand/60 focus:ring-2 focus:ring-brand/15 ${
                      spec?.requires_model_name && !llmModel.trim() ? 'border-danger/50' : ''
                    }`}
                  />
                  <datalist id={`models-${llmProvider}`}>
                    {modelOptions.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                  {modelOptions.length > 0 && (
                    <p className="mt-1 text-[11px] text-muted">
                      点击输入框可展开列表；也支持手动填写列表之外的模型名
                    </p>
                  )}
                </Field>
                <Field
                  label="API Base URL"
                  hint={
                    spec?.default_base_url
                      ? `默认：${spec.default_base_url}`
                      : spec?.requires_base_url
                        ? '必填'
                        : '可选'
                  }
                >
                  <Input
                    placeholder={spec?.default_base_url || 'https://…'}
                    value={llmBaseUrl}
                    onChange={(e) => setLlmBaseUrl(e.target.value)}
                  />
                </Field>
              </div>

              <Field
                label="API Key"
                hint={
                  cfg.llm.values.api_key.has_value
                    ? `已配置（${cfg.llm.values.api_key.masked}），留空表示不修改`
                    : spec?.requires_api_key
                      ? '必填'
                      : '可选'
                }
              >
                <Input
                  type="password"
                  placeholder={cfg.llm.values.api_key.has_value ? '••••••••（已保存）' : '粘贴 API Key'}
                  value={llmKey}
                  onChange={(e) => setLlmKey(e.target.value)}
                />
              </Field>

              <div className="flex flex-wrap items-center gap-3">
                <Button variant="primary" loading={savingLLM} onClick={handleSaveLLM}>
                  保存 LLM 配置
                </Button>
                <Button variant="secondary" loading={testing} onClick={handleTestLLM}>
                  <PlugZap size={15} className="text-brand2" />
                  测试连接
                </Button>
                <span className="text-xs text-muted">保存后立即生效，无需重启服务</span>
              </div>

              {testResult && (
                <div
                  className={`rounded-xl border p-3 text-sm ${
                    testResult.ok
                      ? 'border-success/30 bg-success/5 text-success'
                      : 'border-danger/30 bg-danger/5 text-danger'
                  }`}
                >
                  {testResult.ok ? '✅ ' : '❌ '}
                  {testResult.text}
                </div>
              )}
            </div>
          )}
        </Card>

        {/* ===== 图库检索与默认素材源 ===== */}
        <Card
          title="图库检索与默认素材源"
          icon={<Images size={16} />}
          className={tab === 'assets' ? '' : 'hidden'}
        >
          {!cfg ? (
            <p className="text-sm text-muted">加载中…</p>
          ) : (
            <div className="space-y-4">
              <Field label="默认素材源" hint="创作页新建时默认选中；AI 源需先配置密钥">
                <Select value={videoSource} onChange={(e) => setVideoSource(e.target.value)}>
                  <optgroup label="图库检索">
                    {STOCK_SOURCES.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </optgroup>
                  {enabledAiSources.length > 0 && (
                    <optgroup label="AI 生成视频">
                      {enabledAiSources.map(([id, p]) => (
                        <option key={id} value={id}>
                          {p.label}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  <optgroup label="本地">
                    <option value="local">我的素材库</option>
                  </optgroup>
                </Select>
              </Field>
              <div className="grid gap-4 md:grid-cols-2">
                {STOCK_SOURCES.map((s) => {
                  const state = cfg.materials[s.key]
                  return (
                    <Field
                      key={s.key}
                      label={MATERIAL_LABELS[s.key]}
                      hint={
                        state?.count
                          ? `已配置 ${state.count} 个（${state.masked}…），留空不修改`
                          : '未配置'
                      }
                    >
                      <div className="flex gap-2">
                        <Input
                          placeholder="多个 Key 用逗号分隔"
                          value={materialKeys[s.key] ?? ''}
                          onChange={(e) =>
                            setMaterialKeys((m) => ({ ...m, [s.key]: e.target.value }))
                          }
                        />
                        <a
                          href={MATERIAL_LINKS[s.key]}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex h-10 shrink-0 items-center text-xs text-muted underline decoration-dotted hover:text-brand"
                        >
                          申请
                        </a>
                      </div>
                    </Field>
                  )
                })}
              </div>
              <Button variant="primary" loading={savingMaterials} onClick={handleSaveMaterials}>
                保存图库配置
              </Button>
            </div>
          )}
        </Card>

        {/* ===== AI 生成视频（文生视频） ===== */}
        <Card
          title="AI 生成视频（文生视频）"
          icon={<PlugZap size={16} />}
          className={tab === 'assets' ? '' : 'hidden'}
        >
          {!cfg ? (
            <p className="text-sm text-muted">加载中…</p>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-muted">
                配置密钥与生成模型；保存后创作页才会出现对应素材源，按提交片段计费。
              </p>
              {Object.keys(AI_VIDEO_FIELD_ORDER).map((pid) => renderAiProvider(pid))}
            </div>
          )}
        </Card>

        {/* ===== 配音 TTS：声音克隆 ===== */}
        <Card
          title="声音克隆（SiliconFlow）"
          icon={<Mic size={16} />}
          className={tab === 'tts' ? '' : 'hidden'}
        >
          {!cfg ? (
            <p className="text-sm text-muted">加载中…</p>
          ) : (
            <div className="space-y-5">
              {/* Step 1: SiliconFlow API Key */}
              <div>
                <div className="mb-2 flex items-baseline gap-2">
                  <span className="text-sm font-medium">第一步：填写 SiliconFlow API Key</span>
                  {cfg.tts.siliconflow_api_key.has_value && (
                    <Badge tone="success">
                      已配置（{cfg.tts.siliconflow_api_key.masked}）
                    </Badge>
                  )}
                </div>
                <div className="flex gap-2">
                  <Input
                    type="password"
                    placeholder={cfg.tts.siliconflow_api_key.has_value ? '••••••••（已保存）' : '粘贴 SiliconFlow API Key'}
                    value={sfKey}
                    onChange={(e) => setSfKey(e.target.value)}
                  />
                  <Button
                    variant="secondary"
                    loading={savingSfKey}
                    onClick={async () => {
                      if (!sfKey.trim()) {
                        toast('error', '请先粘贴 Key')
                        return
                      }
                      setSavingSfKey(true)
                      try {
                        await saveSiliconFlowKey(sfKey.trim())
                        toast('success', 'SiliconFlow Key 已保存')
                        setSfKey('')
                        loadConfig()
                      } catch (e) {
                        toast('error', e instanceof Error ? e.message : '保存失败')
                      } finally {
                        setSavingSfKey(false)
                      }
                    }}
                  >
                    保存 Key
                  </Button>
                  <a
                    href="https://cloud.siliconflow.cn/account/ak"
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex h-10 shrink-0 items-center text-xs text-muted underline decoration-dotted hover:text-brand"
                  >
                    注册/获取（免费额度）
                  </a>
                </div>
              </div>

              {/* Step 2: 上传克隆样本 */}
              <div>
                <div className="mb-2 text-sm font-medium">
                  第二步：上传一段 10~30 秒的干净人声
                </div>
                <div className="grid gap-4 md:grid-cols-3">
                  <Field label="声音名称">
                    <Input
                      placeholder="如：我的专属音色"
                      value={cloneName}
                      onChange={(e) => setCloneName(e.target.value)}
                    />
                  </Field>
                  <Field label="录音对应的文字" hint="必须逐字一致，提升自然度">
                    <Input
                      placeholder="录音里念的内容"
                      value={cloneText}
                      onChange={(e) => setCloneText(e.target.value)}
                    />
                  </Field>
                  <Field label="音频文件" hint="wav/mp3 ≤8MB（m4a/aac 会自动转码），安静环境录制">
                    <div className="flex gap-2">
                      <label className="flex-1">
                        <input
                          type="file"
                          accept="audio/*"
                          className="hidden"
                          onChange={(e) => {
                            setCloneFile(e.target.files?.[0] ?? null)
                            e.target.value = ''
                          }}
                        />
                        <span className="inline-flex h-10 w-full cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-line px-3 text-sm text-muted transition-colors hover:border-brand/50 hover:text-brand">
                          <Upload size={15} />
                          {cloneFile ? cloneFile.name.slice(0, 16) : '选择文件'}
                        </span>
                      </label>
                      <Button
                        variant="primary"
                        loading={cloning}
                        disabled={!cloneName.trim() || !cloneText.trim() || !cloneFile || !cfg.tts.siliconflow_api_key.has_value}
                        onClick={async () => {
                          if (!cloneFile) return
                          setCloning(true)
                          try {
                            const r = await cloneVoice(cloneName.trim(), cloneText.trim(), cloneFile)
                            toast('success', `「${r.label}」克隆成功，已加入音色列表`)
                            setCloneName('')
                            setCloneText('')
                            setCloneFile(null)
                            loadConfig()
                          } catch (e) {
                            toast('error', e instanceof Error ? e.message : '克隆失败')
                          } finally {
                            setCloning(false)
                          }
                        }}
                      >
                        开始克隆
                      </Button>
                    </div>
                  </Field>
                </div>
              </div>

              {/* Step 3: 已克隆列表 */}
              <div>
                <div className="mb-2 text-sm font-medium">
                  第三步：已克隆的声音（自动出现在创作页音色列表）
                </div>
                {clonedList.length === 0 ? (
                  <p className="text-xs text-muted">暂无克隆声音，完成上面两步后会显示在这里</p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {clonedList.map((c) => (
                      <span
                        key={c.id}
                        className="inline-flex items-center gap-2 rounded-lg border border-brand/30 bg-brand/5 px-3 py-1.5 text-sm text-ink"
                      >
                        {c.label}
                        <button
                          type="button"
                          disabled={deleting === c.id}
                          onClick={async () => {
                            setDeleting(c.id)
                            try {
                              await deleteClonedVoice(c.id)
                              toast('success', '已从列表移除')
                              loadConfig()
                            } catch (e) {
                              toast('error', e instanceof Error ? e.message : '删除失败')
                            } finally {
                              setDeleting(null)
                            }
                          }}
                          className="text-muted transition-colors hover:text-danger"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>

        {/* ===== 字幕（Whisper） ===== */}
        <Card
          title="字幕（Whisper）"
          icon={<Captions size={16} />}
          className={tab === 'subtitle' ? '' : 'hidden'}
        >
          <p className="text-sm text-muted">
            字幕模型（Whisper 的 model_size、device、compute_type 等）目前仍需在
            config.toml 的 [whisper] 段手动配置，后续版本会在此页面开放编辑。
          </p>
        </Card>

        {/* ===== 通用与系统：外观 ===== */}
        <Card
          title="外观"
          icon={dark ? <Moon size={16} /> : <Sun size={16} />}
          className={tab === 'system' ? '' : 'hidden'}
        >
          <button
            onClick={onToggleTheme}
            className="flex w-full items-center justify-between rounded-xl border border-line bg-panel2 px-4 py-3 text-sm transition-colors hover:border-brand/40"
          >
            <span>当前主题</span>
            <span className="inline-flex items-center gap-2 font-medium text-ink">
              {dark ? (
                <>
                  <Moon size={15} className="text-brand" /> 深色
                </>
              ) : (
                <>
                  <Sun size={15} className="text-warn" /> 浅色
                </>
              )}
            </span>
          </button>
        </Card>

        {/* ===== 通用与系统：API 连接 ===== */}
        <Card
          title="API 连接"
          icon={<Info size={16} />}
          className={tab === 'system' ? '' : 'hidden'}
        >
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted">连接状态</span>
              {apiOnline === null ? (
                <Badge>检测中…</Badge>
              ) : apiOnline ? (
                <Badge tone="success">已连接</Badge>
              ) : (
                <Badge tone="danger">未连接</Badge>
              )}
            </div>
            <p className="text-xs text-muted">
              前端通过 Vite 开发服务器（端口 8502）代理访问本机 FastAPI 服务。
            </p>
          </div>
        </Card>

        {/* ===== 通用与系统：浏览器本地 API Key ===== */}
        <Card
          title="浏览器访问密钥"
          icon={<KeyRound size={16} />}
          className={tab === 'system' ? '' : 'hidden'}
        >
          <Field label="访问密钥（可选）" hint="仅当 config.toml 配置了 app.api_key 时需要填写">
            <div className="flex gap-2">
              <Input
                type="password"
                placeholder="留空 = 未启用鉴权"
                value={apiKey}
                onChange={(e) => setKey(e.target.value)}
              />
              <Button
                variant="primary"
                loading={saved}
                onClick={() => {
                  setApiKey(apiKey.trim())
                  setSaved(true)
                  setTimeout(() => setSaved(false), 1500)
                  toast('success', 'API Key 已保存（仅存于浏览器本地）')
                }}
              >
                保存
              </Button>
            </div>
          </Field>
        </Card>
      </div>
    </div>
  )
}
