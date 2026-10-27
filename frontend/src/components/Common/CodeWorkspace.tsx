import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  Check,
  Code2,
  FileCode2,
  Goal,
  LoaderCircle,
  Paperclip,
  PencilLine,
  Send,
  ShieldCheck,
} from "lucide-react"
import { type ChangeEvent, useEffect, useRef, useState } from "react"
import useAuth from "@/hooks/useAuth"
import {
  applyCodeEdits,
  describeCodeError,
  generateCode,
  getCodeModelConfig,
  getCodeWorkspace,
  readCodeFile,
  saveCodeFile,
  deleteCodeFile,
  importGithubRepository,
  uploadCodeFiles,
  type CodeApiFormat,
  type CodePermission,
  type CodeWorkspaceFile,
} from "@/lib/code"

const apiFormatLabels: Record<CodeApiFormat, string> = {
  openai_chat_completions: "OpenAI Chat Completions 兼容",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
  gemini_generate_content: "Gemini generateContent",
}

const supportedFiles =
  ".c,.cc,.cpp,.css,.go,.h,.hpp,.html,.java,.js,.jsx,.json,.md,.py,.rs,.scss,.sh,.sql,.svelte,.toml,.ts,.tsx,.txt,.vue,.yaml,.yml"

function changedLinePreview(before: string | null | undefined, after: string) {
  if (before == null) {
    return after.split("\n").slice(0, 12).map((line) => `+ ${line}`).join("\n")
  }
  const previous = before.split("\n")
  const next = after.split("\n")
  const preview: string[] = []
  const limit = Math.max(previous.length, next.length)
  for (let index = 0; index < limit && preview.length < 24; index += 1) {
    if (previous[index] === next[index]) continue
    if (index < previous.length) preview.push(`- ${previous[index]}`)
    if (index < next.length && preview.length < 24) preview.push(`+ ${next[index]}`)
  }
  return preview.join("\n") || "没有行级差异"
}

function lineChangeCounts(before: string, after: string) {
  const previous = new Map<string, number>()
  for (const line of before.split("\n")) {
    previous.set(line, (previous.get(line) ?? 0) + 1)
  }
  let removed = before.split("\n").length
  let added = 0
  for (const line of after.split("\n")) {
    const remaining = previous.get(line) ?? 0
    if (remaining > 0) {
      previous.set(line, remaining - 1)
      removed -= 1
    } else {
      added += 1
    }
  }
  return { added, removed }
}

export function CodeWorkspace() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [task, setTask] = useState("")
  const [goal, setGoal] = useState("")
  const [permission, setPermission] = useState<CodePermission>("read_only")
  const [fullAcknowledged, setFullAcknowledged] = useState(false)
  const [approvedPaths, setApprovedPaths] = useState<string[]>([])
  const [selectedPaths, setSelectedPaths] = useState<string[]>([])
  const [selectedFile, setSelectedFile] = useState<CodeWorkspaceFile | null>(null)
  const [draft, setDraft] = useState("")
  const [newPath, setNewPath] = useState("")
  const [githubRepository, setGithubRepository] = useState("")
  const [notice, setNotice] = useState("")
  const [errorNotice, setErrorNotice] = useState("")
  const fileInput = useRef<HTMLInputElement>(null)
  const initializedFiles = useRef(false)

  const workspace = useQuery({
    queryKey: ["code-workspace", user?.id],
    queryFn: getCodeWorkspace,
    enabled: Boolean(user),
  })
  const modelConfig = useQuery({
    queryKey: ["code-model-config"],
    queryFn: getCodeModelConfig,
    enabled: Boolean(user),
  })
  const fileContent = useQuery({
    queryKey: ["code-workspace-file", user?.id, selectedFile?.path],
    queryFn: () => readCodeFile(selectedFile!.path),
    enabled: Boolean(selectedFile && user),
  })
  useEffect(() => {
    if (!selectedFile || fileContent.data === undefined) return
    setDraft(fileContent.data)
  }, [selectedFile, fileContent.data])

  const upload = useMutation({
    mutationFn: uploadCodeFiles,
    onSuccess: async (result, files) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["code-workspace", user?.id] }),
        ...files.map((file) =>
          queryClient.invalidateQueries({
            queryKey: ["code-workspace-file", user?.id, file.name],
          }),
        ),
      ])
      const newPaths = files.map((file) => file.name)
      setSelectedPaths((current) => Array.from(new Set([...current, ...newPaths])).slice(0, 20))
      setNotice(`已加入 ${files.length} 个文件，模型只会读取你勾选的附件。`)
      setErrorNotice("")
      if (result.files.length && !selectedFile) setSelectedFile(result.files[0])
    },
    onError: (error) => setErrorNotice(describeCodeError(error)),
  })
  const githubImport = useMutation({
    mutationFn: importGithubRepository,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["code-workspace", user?.id] })
      setSelectedPaths((current) => Array.from(new Set([
        ...current,
        ...result.files.map((file) => file.path),
      ])).slice(0, 20))
      setNotice(`已从 ${result.repository} 的 ${result.branch} 分支导入 ${result.imported} 个文本文件。`)
      setErrorNotice("")
      setGithubRepository("")
    },
    onError: (error) => setErrorNotice(describeCodeError(error)),
  })
  const saveFile = useMutation({
    mutationFn: () => saveCodeFile(selectedFile?.path ?? "", draft),
    onSuccess: async () => {
      const path = selectedFile?.path
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["code-workspace", user?.id] }),
        queryClient.invalidateQueries({ queryKey: ["code-workspace-file", user?.id, path] }),
        queryClient.invalidateQueries({ queryKey: ["code-edit-baselines", user?.id] }),
      ])
      setNotice("已保存到当前账号的隔离工作区。")
      setErrorNotice("")
    },
    onError: (error) => setErrorNotice(describeCodeError(error)),
  })
  const removeFile = useMutation({
    mutationFn: () => deleteCodeFile(selectedFile?.path ?? ""),
    onSuccess: async () => {
      const path = selectedFile?.path
      setSelectedFile(null)
      setDraft("")
      setSelectedPaths((current) => current.filter((item) => item !== path))
      await queryClient.invalidateQueries({ queryKey: ["code-workspace", user?.id] })
      setNotice("已从当前工作区删除该文件。")
      setErrorNotice("")
    },
    onError: (error) => setErrorNotice(describeCodeError(error)),
  })
  const generation = useMutation({
    mutationFn: generateCode,
    onSuccess: () => {
      setApprovedPaths([])
      setNotice("")
      setErrorNotice("")
    },
    onError: (error) => setErrorNotice(describeCodeError(error)),
  })
  const apply = useMutation({
    mutationFn: applyCodeEdits,
    onSuccess: async (result, input) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["code-workspace", user?.id] }),
        ...input.edits.map((edit) =>
          queryClient.invalidateQueries({
            queryKey: ["code-workspace-file", user?.id, edit.path],
          }),
        ),
      ])
      setSelectedPaths((current) => Array.from(new Set([...current, ...result.files.map((file) => file.path)])).slice(0, 20))
      setNotice("已将审阅通过的改动写入当前账号的应用沙箱。")
      setErrorNotice("")
      generation.reset()
      setApprovedPaths([])
    },
    onError: (error) => setErrorNotice(describeCodeError(error)),
  })

  useEffect(() => {
    if (!user) return
    const savedGoal = localStorage.getItem(`devassist-code-goal:${user.id}`)
    const savedPermission = localStorage.getItem(`devassist-code-permission:${user.id}`)
    if (savedGoal) setGoal(savedGoal)
    if (
      savedPermission === "read_only" ||
      savedPermission === "workspace_modify" ||
      savedPermission === "full"
    ) setPermission(savedPermission)
  }, [user])

  useEffect(() => {
    if (!workspace.data || initializedFiles.current) return
    setSelectedPaths(workspace.data.files.slice(0, 20).map((file) => file.path))
    if (workspace.data.files[0]) setSelectedFile(workspace.data.files[0])
    initializedFiles.current = true
  }, [workspace.data])

  const setCurrentPermission = (value: CodePermission) => {
    setPermission(value)
    setFullAcknowledged(false)
    if (user) localStorage.setItem(`devassist-code-permission:${user.id}`, value)
  }

  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ""
    if (!files.length) return
    if (files.length > 10) {
      setErrorNotice("一次最多选择 10 个代码附件。")
      return
    }
    setNotice("")
    await upload.mutateAsync(files).catch(() => undefined)
  }

  const requestGeneration = (planOnly: boolean) => {
    const trimmedTask = task.trim()
    if (!trimmedTask) {
      setErrorNotice("先写下要完成的代码任务。")
      return
    }
    setErrorNotice("")
    setNotice("")
    generation.mutate({
      task: trimmedTask,
      goal: goal.trim(),
      planOnly,
      permission,
      fullPermissionsAcknowledged: fullAcknowledged,
      workspacePaths: selectedPaths,
    })
  }

  const codeBusy = upload.isPending || githubImport.isPending || saveFile.isPending || removeFile.isPending || generation.isPending || apply.isPending
  const result = generation.data
  const baselines = useQuery({
    queryKey: ["code-edit-baselines", user?.id, result?.request_id],
    queryFn: async () => {
      const known = new Set(workspace.data?.files.map((file) => file.path) ?? [])
      const entries = await Promise.all(
        (result?.edits ?? []).map(async (edit) => {
          if (!known.has(edit.path)) return [edit.path, null] as const
          try {
            return [edit.path, await readCodeFile(edit.path)] as const
          } catch {
            return [edit.path, null] as const
          }
        }),
      )
      return Object.fromEntries(entries)
    },
    enabled: Boolean(user && result && result.edits.length > 0),
  })
  const modelLabel = modelConfig.data
    ? [
        apiFormatLabels[modelConfig.data.api_format],
        modelConfig.data.model,
        modelConfig.data.base_url_host ? `主机 ${modelConfig.data.base_url_host}` : null,
        modelConfig.data.local ? "本机服务" : null,
      ].filter(Boolean).join(" · ")
    : "正在读取模型配置…"

  return (
    <main className="da-code-workbench" aria-label="编程智能体工作区">
      <aside className="da-code-files" aria-label="沙箱文件">
        <div className="da-code-pane-heading">
          <div>
            <h2><FileCode2 size={19} aria-hidden="true" /> 隔离工作区</h2>
            <p>仅保存本账号上传、从公开 GitHub 仓库导入或确认写入的文本文件</p>
          </div>
          <button
            type="button"
            className="da-icon-button"
            aria-label="上传代码附件"
            disabled={codeBusy}
            onClick={() => fileInput.current?.click()}
          >
            <Paperclip size={19} aria-hidden="true" />
          </button>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={supportedFiles}
            hidden
            onChange={(event) => void handleUpload(event)}
          />
        </div>
        <form
          className="da-code-github"
          onSubmit={(event) => {
            event.preventDefault()
            const repository = githubRepository.trim()
            if (!repository) {
              setErrorNotice("请填写 GitHub 仓库地址。")
              return
            }
            setErrorNotice("")
            githubImport.mutate(repository)
          }}
        >
          <label htmlFor="da-github-repository">公开 GitHub 仓库</label>
          <input
            id="da-github-repository"
            value={githubRepository}
            placeholder="https://github.com/owner/repo"
            disabled={codeBusy}
            onChange={(event) => setGithubRepository(event.target.value)}
          />
          <button type="submit" className="da-secondary" disabled={codeBusy}>
            {githubImport.isPending ? <LoaderCircle className="animate-spin" size={17} /> : null}
            导入仓库
          </button>
        </form>
        <div className="da-code-file-meta">
          {workspace.data?.files.length ?? 0} 个文件 · {Math.ceil((workspace.data?.total_bytes ?? 0) / 1024)} KiB / 4096 KiB
          {workspace.data && workspace.data.files.length > 0 && (
            <span>
              <button type="button" className="da-text-button" disabled={codeBusy} onClick={() => setSelectedPaths(workspace.data.files.slice(0, 20).map((file) => file.path))}>
                勾选前 20 个
              </button>
              <button type="button" className="da-text-button" disabled={codeBusy} onClick={() => setSelectedPaths([])}>
                取消勾选
              </button>
            </span>
          )}
        </div>
        {workspace.isLoading && <p className="da-code-muted">正在读取沙箱…</p>}
        {workspace.isError && <p role="alert" className="da-inline-error">无法读取隔离工作区。</p>}
        {!workspace.isLoading && !workspace.data?.files.length && (
          <div className="da-code-empty">
            <Code2 size={24} aria-hidden="true" />
            <p>上传代码、配置或项目文档开始工作。</p>
          </div>
        )}
        <div className="da-code-file-list">
          {workspace.data?.files.map((file) => (
            <div key={file.path} className="da-code-file-row">
              <label title="把此文件作为模型上下文">
                <input
                  type="checkbox"
                  checked={selectedPaths.includes(file.path)}
                  disabled={!selectedPaths.includes(file.path) && selectedPaths.length >= 20}
                  onChange={(event) => setSelectedPaths((current) => event.target.checked
                    ? Array.from(new Set([...current, file.path]))
                    : current.filter((path) => path !== file.path))}
                />
              </label>
              <button
                type="button"
                className={selectedFile?.path === file.path ? "is-selected" : ""}
                onClick={() => setSelectedFile(file)}
              >
                <span>{file.path}</span>
                <small>{Math.ceil(file.size_bytes / 1024)} KiB</small>
              </button>
            </div>
          ))}
        </div>
        {selectedFile && (
          <section className="da-code-file-preview" aria-label={`${selectedFile.path} 编辑器`}>
            <div className="da-code-pane-heading">
              <h3>{selectedFile.path}</h3>
              <span>{draft !== fileContent.data ? "未保存" : "已打开"}</span>
            </div>
            {fileContent.isLoading ? <p className="da-code-muted">正在打开文件…</p> : (
              <textarea
                aria-label={`${selectedFile.path} 的内容`}
                className="da-code-editor"
                value={draft}
                spellCheck={false}
                disabled={codeBusy}
                onChange={(event) => setDraft(event.target.value)}
              />
            )}
            <div className="da-code-editor-actions">
              <button type="button" className="da-primary" disabled={codeBusy || !selectedFile || draft === fileContent.data} onClick={() => saveFile.mutate()}>
                保存文件
              </button>
              <button type="button" className="da-secondary" disabled={codeBusy || !selectedFile} onClick={() => removeFile.mutate()}>
                删除文件
              </button>
            </div>
          </section>
        )}
        <form
          className="da-code-github"
          onSubmit={(event) => {
            event.preventDefault()
            const path = newPath.trim()
            if (!path) {
              setErrorNotice("请填写新文件路径，例如 src/app.py。")
              return
            }
            setErrorNotice("")
            saveCodeFile(path, "").then(async (result) => {
              await queryClient.invalidateQueries({ queryKey: ["code-workspace", user?.id] })
              const created = result.files.find((file) => file.path === path)
              if (created) setSelectedFile(created)
              setNewPath("")
              setNotice("已在当前工作区新建空文件。")
            }).catch((error: unknown) => setErrorNotice(describeCodeError(error)))
          }}
        >
          <label htmlFor="da-new-file">新建文件</label>
          <input
            id="da-new-file"
            value={newPath}
            placeholder="src/app.py"
            disabled={codeBusy}
            onChange={(event) => setNewPath(event.target.value)}
          />
          <button type="submit" className="da-secondary" disabled={codeBusy}>新建</button>
        </form>
      </aside>

      <section className="da-code-session">
        <div className="da-code-section-title">
          <div>
            <h2><Code2 size={21} aria-hidden="true" /> 编程任务</h2>
            <p>先规划，再审阅改动；模型没有终端或主机目录访问能力。</p>
          </div>
          <span className={`da-code-model-state ${modelConfig.data?.configured ? "is-ready" : "is-missing"}`}>
            <span aria-hidden="true" />{modelConfig.data?.configured ? "模型已配置" : "模型未配置"}
          </span>
        </div>
        <p className="da-code-model-label">{modelLabel}</p>

        <label className="da-code-field">
          <span><Goal size={17} aria-hidden="true" /> Goal / 项目目标</span>
          <textarea
            rows={2}
            value={goal}
            onChange={(event) => {
              setGoal(event.target.value)
              if (user) localStorage.setItem(`devassist-code-goal:${user.id}`, event.target.value)
            }}
            placeholder="例如：构建一个可演示的 Python 智能体项目"
            disabled={codeBusy}
          />
        </label>

        <form className="da-code-task" onSubmit={(event) => { event.preventDefault(); requestGeneration(false) }}>
          <label htmlFor="da-code-task">当前任务</label>
          <textarea
            id="da-code-task"
            rows={4}
            value={task}
            onChange={(event) => setTask(event.target.value)}
            placeholder="描述希望实现的功能，或让智能体解释上传的代码。"
            disabled={codeBusy}
          />
          <p className="da-code-hint">最多读取 20 个已勾选文件，合计不超过 64 KiB；项目文件内容会发送给已配置的模型服务。</p>
          <div className="da-code-task-actions">
            <button type="button" className="da-secondary" disabled={codeBusy || !modelConfig.data?.configured || (permission === "full" && !fullAcknowledged)} onClick={() => requestGeneration(true)}>
              {generation.isPending && generation.variables?.planOnly ? <LoaderCircle className="animate-spin" size={17} /> : <Goal size={17} />}
              制定计划
            </button>
            <button type="submit" className="da-primary" disabled={codeBusy || !modelConfig.data?.configured || (permission === "full" && !fullAcknowledged)}>
              {generation.isPending && !generation.variables?.planOnly ? <LoaderCircle className="animate-spin" size={17} /> : <Send size={17} />}
              生成改动预览
            </button>
          </div>
        </form>

        {errorNotice && <p className="da-inline-error" role="alert">{errorNotice}</p>}
        {notice && <p className="da-notice" role="status">{notice}</p>}
        {generation.isPending && <div className="da-code-loading" role="status"><LoaderCircle className="animate-spin" /> 正在请求模型生成结果…</div>}
        {result && (
          <section className="da-code-result" aria-live="polite">
            <div className="da-code-result-heading">
              <div><h3>{generation.variables?.planOnly ? "执行计划" : "计划与候选改动"}</h3><p>请求标识：{result.request_id || "本地"}</p></div>
              {result.edits.length > 0 && <span>{result.edits.length} 个文件改动</span>}
            </div>
            {result.plan.length > 0 && <ol className="da-code-plan">{result.plan.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol>}
            {result.summary && <p className="da-code-summary">{result.summary}</p>}
            {generation.variables?.planOnly && result.plan.length > 0 && (
              <button type="button" className="da-text-button" onClick={() => setTask(`请按以下计划实现：\n${result.plan.map((step, index) => `${index + 1}. ${step}`).join("\n")}`)}>
                按此计划生成改动预览
              </button>
            )}
            {result.edits.map((edit) => {
              const before = baselines.data?.[edit.path]
              const counts = before == null ? null : lineChangeCounts(before, edit.content)
              const changeLabel = counts == null
                ? "新文件"
                : counts.added === 0 && counts.removed === 0
                  ? "与当前文件相同"
                  : `约 +${counts.added} 行 / -${counts.removed} 行`
              return (
                <article className="da-code-edit" key={edit.path}>
                  <label>
                    <input
                      type="checkbox"
                      checked={approvedPaths.includes(edit.path)}
                      disabled={permission === "read_only" || codeBusy}
                      onChange={(event) => setApprovedPaths((current) => event.target.checked
                        ? Array.from(new Set([...current, edit.path]))
                        : current.filter((path) => path !== edit.path))}
                    />
                    <strong>{edit.path}</strong>
                    <small>{changeLabel}</small>
                  </label>
                  <button
                    type="button"
                    className="da-text-button"
                    onClick={() => {
                      void navigator.clipboard.writeText(edit.content).then(
                        () => {
                          setNotice(`已复制 ${edit.path}。`)
                          setErrorNotice("")
                        },
                        () => setErrorNotice("复制失败，请手动选择代码。"),
                      )
                    }}
                  >
                    复制候选代码
                  </button>
                  <pre className="da-code-diff">{changedLinePreview(before, edit.content)}</pre>
                  <details>
                    <summary>完整候选内容</summary>
                    <pre>{edit.content}</pre>
                  </details>
                </article>
              )
            })}
            {result.edits.length > 0 && (
              <div className="da-code-apply-box">
                {permission === "read_only" ? (
                  <p><ShieldCheck size={17} /> 当前为仅可查看。候选代码可复制，切换权限后才能写入沙箱。</p>
                ) : permission === "workspace_modify" ? (
                  <p><PencilLine size={17} /> 逐个勾选已审阅的文件改动，再确认写入。</p>
                ) : (
                  <p><AlertTriangle size={17} /> 完整沙箱权限已确认；点击下方按钮会一次写入全部候选文件。</p>
                )}
                <button
                  type="button"
                  className="da-primary"
                  disabled={codeBusy || permission === "read_only" || (permission === "workspace_modify" && approvedPaths.length === 0) || (permission === "full" && !fullAcknowledged)}
                  onClick={() => apply.mutate({
                    edits: permission === "workspace_modify" ? result.edits.filter((edit) => approvedPaths.includes(edit.path)) : result.edits,
                    permission,
                    approved: true,
                    fullPermissionsAcknowledged: fullAcknowledged,
                  })}
                >
                  {apply.isPending ? <LoaderCircle className="animate-spin" size={17} /> : <Check size={17} />}
                  确认写入隔离工作区
                </button>
              </div>
            )}
          </section>
        )}
      </section>

      <aside className="da-code-controls" aria-label="权限与模型设置">
        <section>
          <h2><ShieldCheck size={19} aria-hidden="true" /> 工作区权限</h2>
          <label className="da-code-permission-option">
            <input type="radio" name="code-permission" checked={permission === "read_only"} onChange={() => setCurrentPermission("read_only")} />
            <span><strong>仅可查看</strong><small>读取已上传文件、制定计划和查看候选代码。</small></span>
          </label>
          <label className="da-code-permission-option">
            <input type="radio" name="code-permission" checked={permission === "workspace_modify"} onChange={() => setCurrentPermission("workspace_modify")} />
            <span><strong>工作区修改</strong><small>逐项审阅并确认后，写入此账号的隔离工作区。</small></span>
          </label>
          <label className="da-code-permission-option">
            <input type="radio" name="code-permission" checked={permission === "full"} onChange={() => setCurrentPermission("full")} />
            <span><strong>完整权限</strong><small>完整文件读写权限仅作用于此应用沙箱。</small></span>
          </label>
          {permission === "full" && (
            <div className="da-code-warning" role="alert">
              <strong><AlertTriangle size={17} /> 完整权限警告</strong>
              <p>此权限允许模型候选改动写入当前账号的应用沙箱。不会访问主机其他目录；当前版本未开放终端命令执行。</p>
              <label><input type="checkbox" checked={fullAcknowledged} onChange={(event) => setFullAcknowledged(event.target.checked)} /> 我了解范围并确认启用完整沙箱权限</label>
            </div>
          )}
        </section>
        <section className="da-code-boundary">
          <h2>接入模型</h2>
          <ul>
            <li>只在项目根目录 `.env` 配置，改完后重启后端。这个页面不接收密钥，也只显示主机名。</li>
            <li>本地 Ollama：`CODE_API_FORMAT=openai_chat_completions`，`CODE_BASE_URL=http://127.0.0.1:11434/v1`，`CODE_MODEL` 填本机已有模型名。`CODE_API_KEY` 可以留空。</li>
            <li>第三方 OpenAI 兼容服务：同样用 `openai_chat_completions`，`CODE_BASE_URL` 填对方的 API 根地址，`CODE_MODEL` 填模型名，`CODE_API_KEY` 只写在本机 `.env`。</li>
            <li>`openai_responses`、`anthropic_messages`、`gemini_generate_content` 仍然可用，都需要各自的真实密钥。</li>
            <li>模型输出不会自动执行；写入沙箱前必须确认。</li>
          </ul>
          {!modelConfig.data?.configured && !modelConfig.isLoading && (
            <p className="da-code-config-help">当前还不能调用代码模型。请按上面的方式配置后重启后端。</p>
          )}
        </section>
      </aside>
    </main>
  )
}
