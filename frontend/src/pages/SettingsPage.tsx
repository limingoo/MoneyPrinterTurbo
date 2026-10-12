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
} from '../api/endpoints'
import { Upload, Mic } from 'lucide-react'

const MATERIAL_LABELS: Record<string, string> = {
  pexels_api_keys: 'Pexels API Keys',
  pixabay_api_keys: 'Pixabay API Keys',
  coverr_api_keys: 'Coverr API Keys',
  wavespeed_api_keys: 'Wavespeed API Keys',
}

const MATERIAL_LINKS: Record<string, string> = {
  pexels_api_keys: 'https://www.pexels.com/api/',
  pixabay_api_keys: 'https://pixabay.com/api/docs/',
  coverr_api_keys: 'https://coverr.co/',
  wavespeed_api_keys: 'https://wavespeed.ai/',
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

  const loadConfig = () => {
    getConfig()
      .then((c) => {
        setCfg(c)
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
      if (Object.keys(payload).length === 0) {
        toast('error', '没有修改需要保存')
        return
      }
      await updateConfig({ materials: payload })
      toast('success', '素材源密钥已保存并即时生效')
      loadConfig()
    } catch (e) {
      toast('error', e instanceof Error ? e.message : '保存失败')
    } finally {
      setSavingMaterials(false)
    }
  }

  return (
    <div className="animate-fade-up space-y-5">
      <div className="flex items-center gap-3">
        <SettingsIcon size={22} className="text-brand" />
        <h1 className="text-xl font-bold">设置</h1>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* ===== LLM 配置 ===== */}
        <Card title="AI 模型（LLM）" icon={<BrainCircuit size={16} />} className="lg:col-span-2">
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

        {/* ===== 素材源 ===== */}
        <Card title="素材源 API Keys" icon={<Images size={16} />} className="lg:col-span-2">
          {!cfg ? (
            <p className="text-sm text-muted">加载中…</p>
          ) : (
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                {Object.keys(MATERIAL_LABELS).map((key) => {
                  const state = cfg.materials[key]
                  return (
                    <Field
                      key={key}
                      label={MATERIAL_LABELS[key]}
                      hint={
                        state?.count
                          ? `已配置 ${state.count} 个（${state.masked}…），留空不修改`
                          : '未配置'
                      }
                    >
                      <div className="flex gap-2">
                        <Input
                          placeholder="多个 Key 用逗号分隔"
                          value={materialKeys[key] ?? ''}
                          onChange={(e) =>
                            setMaterialKeys((m) => ({ ...m, [key]: e.target.value }))
                          }
                        />
                        {MATERIAL_LINKS[key] && (
                          <a
                            href={MATERIAL_LINKS[key]}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex h-10 shrink-0 items-center text-xs text-muted underline decoration-dotted hover:text-brand"
                          >
                            申请
                          </a>
                        )}
                      </div>
                    </Field>
                  )
                })}
              </div>
              <Button variant="primary" loading={savingMaterials} onClick={handleSaveMaterials}>
                保存素材源密钥
              </Button>
            </div>
          )}
        </Card>

        {/* ===== 声音克隆 ===== */}
        <Card title="声音克隆" icon={<Mic size={16} />} className="lg:col-span-2">
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
                  <Field label="音频文件" hint="wav/mp3 ≤8MB，安静环境录制">
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

        {/* ===== 外观 ===== */}
        <Card title="外观" icon={dark ? <Moon size={16} /> : <Sun size={16} />}>
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

        {/* ===== API 连接 ===== */}
        <Card title="API 连接" icon={<Info size={16} />}>
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

        {/* ===== 浏览器本地 API Key ===== */}
        <Card title="浏览器访问密钥" icon={<KeyRound size={16} />} className="lg:col-span-2">
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
