import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import {
  Bot,
  Check,
  Code2,
  Copy,
  FileText,
  LoaderCircle,
  MessageSquare,
  Paperclip,
  Search,
  Send,
  TriangleAlert,
  UserRound,
  X,
} from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"
import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from "react"
import { ChatHistory } from "@/components/Common/ChatHistory"
import { CodeWorkspace } from "@/components/Common/CodeWorkspace"
import { DevAssistHeader } from "@/components/Common/DevAssistLayout"
import { DocumentScope } from "@/components/Common/DocumentScope"
import { KnowledgeSourcePanel } from "@/components/Common/KnowledgeSourcePanel"
import {
  RetrievalMethod,
  RetrievalStatus,
} from "@/components/Common/RetrievalMethod"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import { describeChatError, sendChatMessage } from "@/lib/chat"
import {
  type RetrievalMethod as RetrievalMode,
  describeKnowledgeError,
  listDocuments,
  searchDocuments,
  validDocumentId,
  uploadDocument,
} from "@/lib/knowledge"

export const Route = createFileRoute("/_layout/chat")({
  component: Chat,
  head: () => ({ meta: [{ title: "AI 问答 - DevAssist" }] }),
  validateSearch: (
    search: Record<string, unknown>,
  ): { question?: string; document?: string; method?: RetrievalMode } => ({
    method:
      search.method === "vector" || search.method === "hybrid"
        ? search.method
        : undefined,
    question:
      typeof search.question === "string"
        ? Array.from(search.question).slice(0, 4000).join("")
        : undefined,
    document: validDocumentId(search.document),
  }),
})

function Chat() {
  const {
    question,
    document: requestedDocument,
    method: requestedMethod,
  } = Route.useSearch()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [message, setMessage] = useState(question ?? "")
  const [mode, setMode] = useState<"rag" | "code">("rag")
  const [useDocuments, setUseDocuments] = useState(
    Boolean(question || requestedDocument),
  )
  const [scope, setScope] = useState(requestedDocument ?? "")
  const [method, setMethod] = useState<RetrievalMode>(requestedMethod ?? "bm25")
  const [submittedMessage, setSubmittedMessage] = useState("")
  const [validationError, setValidationError] = useState("")
  const submitting = useRef(false)
  const pendingRequest = useRef<{ signature: string; key: string } | null>(null)
  const mutation = useMutation({
    mutationFn: sendChatMessage,
    retry: false,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["chat-history", user?.id] }),
  })
  const retrieval = useMutation({
    mutationFn: (input: { query: string; documentId?: string }) =>
      searchDocuments(input.query, input.documentId, method),
    retry: false,
  })
  const documents = useQuery({
    queryKey: ["documents", user?.id],
    queryFn: listDocuments,
    enabled: Boolean(user),
  })
  const [copyNotice, setCopyNotice] = useState("")
  const [uploadingDocument, setUploadingDocument] = useState(false)
  const [attachedDocumentId, setAttachedDocumentId] = useState("")
  const attachmentInput = useRef<HTMLInputElement>(null)
  const busy = mutation.isPending || retrieval.isPending || uploadingDocument
  const sources = mutation.data?.sources ?? retrieval.data?.sources ?? []
  useEffect(() => {
    setMessage(question ?? "")
    setScope(requestedDocument ?? "")
    setMethod(requestedMethod ?? "bm25")
    if (question || requestedDocument) setUseDocuments(true)
  }, [question, requestedDocument, requestedMethod])
  useEffect(() => {
    if (!user) return
    const savedMode = localStorage.getItem(`devassist-chat-mode:${user.id}`)
    if (savedMode === "rag" || savedMode === "code") setMode(savedMode)
  }, [user])
  const characterCount = Array.from(message.trim()).length
  const error = mutation.isError ? describeChatError(mutation.error) : null

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current) return

    const trimmedMessage = message.trim()
    const length = Array.from(trimmedMessage).length
    if (length < 1 || length > 4000) {
      setValidationError("请输入去除首尾空白后 1～4000 个字符的问题。")
      return
    }

    submitting.current = true
    setValidationError("")
    setCopyNotice("")
    setSubmittedMessage(trimmedMessage)
    retrieval.reset()
    mutation.reset()
    const signature = JSON.stringify([
      trimmedMessage,
      useDocuments,
      useDocuments ? scope : "",
      useDocuments ? method : "",
    ])
    if (!pendingRequest.current || pendingRequest.current.signature !== signature) {
      pendingRequest.current = { signature, key: crypto.randomUUID() }
    }
    try {
      await mutation.mutateAsync({
        message: trimmedMessage,
        useDocuments,
        retrievalMethod: method,
        documentId: scope || undefined,
        clientRequestId: pendingRequest.current.key,
      })
      pendingRequest.current = null
    } catch {
      // The mutation state renders a safe, user-facing error below.
    } finally {
      submitting.current = false
    }
  }

  async function handleRetrieve() {
    if (submitting.current) return
    const trimmed = message.trim()
    if (!trimmed || Array.from(trimmed).length > 1000) {
      setValidationError("只检索原文时请输入 1～1000 个字符的关键词。")
      return
    }
    submitting.current = true
    setValidationError("")
    setCopyNotice("")
    setSubmittedMessage(trimmed)
    mutation.reset()
    retrieval.reset()
    try {
      await retrieval.mutateAsync({
        query: trimmed,
        documentId: scope || undefined,
      })
    } catch {
      /* Show the safe retrieval error. */
    } finally {
      submitting.current = false
    }
  }

  async function handleDocumentAttachment(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ""
    if (!file) return
    if (file.size > 256 * 1024) {
      setValidationError("RAG 附件不能超过 256 KiB。")
      return
    }
    setUploadingDocument(true)
    setValidationError("")
    try {
      const result = await uploadDocument(file)
      await queryClient.invalidateQueries({ queryKey: ["documents", user?.id] })
      setAttachedDocumentId(result.document.id)
      setScope(result.document.id)
      setUseDocuments(true)
    } catch (error) {
      setValidationError(describeKnowledgeError(error))
    } finally {
      setUploadingDocument(false)
    }
  }

  async function copyAnswer() {
    if (!mutation.data) return
    try {
      await navigator.clipboard.writeText(mutation.data.answer)
      setCopyNotice("已复制")
    } catch {
      setCopyNotice("浏览器未允许复制，请选中回答文字复制。")
    }
  }

  return (
    <>
      <DevAssistHeader code={mode === "code"} />
      <div className="da-mode-switch" role="group" aria-label="工作模式">
        <button
          type="button"
          aria-pressed={mode === "rag"}
          className={mode === "rag" ? "is-active" : ""}
          onClick={() => {
            setMode("rag")
            if (user) localStorage.setItem(`devassist-chat-mode:${user.id}`, "rag")
          }}
        >
          <Search size={18} aria-hidden="true" /> RAG 知识问答
        </button>
        <button
          type="button"
          aria-pressed={mode === "code"}
          className={mode === "code" ? "is-active" : ""}
          onClick={() => {
            setMode("code")
            if (user) localStorage.setItem(`devassist-chat-mode:${user.id}`, "code")
          }}
        >
          <Code2 size={18} aria-hidden="true" /> 编程智能体
        </button>
      </div>
      {mode === "rag" && user && <ChatHistory userId={user.id} />}
      {mode === "code" ? <CodeWorkspace /> : (
      <div className="da-workbench">
        <section className="da-question-column" aria-label="问答">
          {submittedMessage && (
            <div className="da-question-block">
              <div className="da-section-label">
                <UserRound size={23} aria-hidden="true" />
                我的问题
              </div>
              <p>{submittedMessage}</p>
            </div>
          )}
          <div className="da-answer-area" aria-live="polite" aria-busy={busy}>
            {busy && (
              <div className="da-state" role="status">
                <LoaderCircle
                  className="animate-spin"
                  size={26}
                  aria-hidden="true"
                />
                <h2>
                  {retrieval.isPending
                    ? "正在查找原文…"
                    : "正在生成回答，请稍候…"}
                </h2>
                <p>
                  {retrieval.isPending
                    ? "正在检索文档，不生成回答。"
                    : "问题已发送，请稍候。"}
                </p>
              </div>
            )}
            {error && (
              <div role="alert" className="da-state da-error">
                <TriangleAlert size={26} aria-hidden="true" />
                <h2>暂时无法生成回答</h2>
                <p>{error.message}</p>
                {error.requestId && <small>请求标识：{error.requestId}</small>}
                <Link to="/knowledge">查看我的知识库</Link>
              </div>
            )}
            {retrieval.isError && (
              <div role="alert" className="da-state da-error">
                <h2>暂时无法检索</h2>
                <p>{describeKnowledgeError(retrieval.error)}</p>
              </div>
            )}
            {retrieval.data && !mutation.data && (
              <div className="da-state">
                <Search size={28} aria-hidden="true" />
                <h2>
                  {sources.length
                    ? "找到相关原文，可以开始核对。"
                    : "没有匹配的片段"}
                </h2>
                <p>
                  {sources.length
                    ? "来源面板显示真实检索结果。此操作没有请求模型生成回答。"
                    : "请尝试文档中的关键词，或先导入相关资料。"}
                </p>
              </div>
            )}
            {mutation.data && (
              <section aria-label="AI 回答" className="da-answer">
                <div className="da-section-label">
                  <Bot size={23} aria-hidden="true" />
                  AI 回答
                </div>
                <h2>本次回答</h2>
                <p className="da-answer-text">{mutation.data.answer}</p>
                <button
                  type="button"
                  className="da-secondary da-copy"
                  onClick={copyAnswer}
                >
                  {copyNotice === "已复制" ? (
                    <Check size={18} aria-hidden="true" />
                  ) : (
                    <Copy size={18} aria-hidden="true" />
                  )}
                  {copyNotice === "已复制" ? "已复制" : "复制回答"}
                </button>
                {copyNotice && (
                  <p role="status" className="da-note">
                    {copyNotice}
                  </p>
                )}
                <p className="da-note">
                  {sources.length
                    ? "来源为本次检索片段，请结合原文核对。"
                    : "本次未使用项目文档。"}
                </p>
                {mutation.data.request_id && (
                  <p className="da-request">
                    请求标识：{mutation.data.request_id}
                  </p>
                )}
              </section>
            )}
            {!busy &&
              !error &&
              !retrieval.isError &&
              !retrieval.data &&
              !mutation.data && (
                <div className="da-state">
                  <MessageSquare size={30} aria-hidden="true" />
                  <h2>从一个项目问题开始</h2>
                  <p>
                    输入问题后发送，或勾选知识库，只检索原文。文档检索无需模型密钥。
                  </p>
                  <button
                    type="button"
                    className="da-text-button"
                    onClick={() => {
                      setMessage("读取超时是否应该重试？")
                      setUseDocuments(true)
                    }}
                  >
                    试试：读取超时是否应该重试？
                  </button>
                </div>
              )}
          </div>
          <RetrievalStatus
            method={mutation.data?.retrieval_method ?? retrieval.data?.method}
            warning={
              mutation.data?.retrieval_warning ?? retrieval.data?.warning
            }
          />
          <form onSubmit={handleSubmit} className="da-composer">
            <label htmlFor="chat-message">你的问题</label>
            <textarea
              id="chat-message"
              value={message}
              onChange={(event) => {
                setMessage(event.target.value)
                setValidationError("")
              }}
              disabled={busy}
              rows={3}
              placeholder="例如：如何设置异步请求的超时？"
              aria-describedby="chat-message-help"
              aria-invalid={Boolean(validationError)}
            />
            <p id="chat-message-help" className="da-composer-help">
              {characterCount} / 4000 字符 · 成功回答会保存在历史记录中
            </p>
            {useDocuments && (
              <DocumentScope
                value={scope}
                documents={documents.data?.data ?? []}
                disabled={busy}
                onChange={(value) => {
                  setScope(value)
                  mutation.reset()
                  retrieval.reset()
                  setSubmittedMessage("")
                  setValidationError("")
                }}
              />
            )}
            {useDocuments && (
              <RetrievalMethod
                value={method}
                disabled={busy}
                onChange={(value) => {
                  setMethod(value)
                  mutation.reset()
                  retrieval.reset()
                  setSubmittedMessage("")
                }}
              />
            )}
            <div className="da-attachment-row">
                <input
                  ref={attachmentInput}
                  type="file"
                  accept=".md,.txt,text/plain,text/markdown"
                  hidden
                  disabled={busy}
                  onChange={(event) => void handleDocumentAttachment(event)}
                />
                <button
                  type="button"
                  className="da-secondary"
                  disabled={busy}
                  onClick={() => attachmentInput.current?.click()}
                >
                  {uploadingDocument ? <LoaderCircle className="animate-spin" size={17} /> : <Paperclip size={17} />}
                  添加文档附件
                </button>
                <small>Markdown / TXT，单个文件最大 256 KiB</small>
                {attachedDocumentId && (
                  <span className="da-attachment-chip">
                    <FileText size={16} aria-hidden="true" />
                    {documents.data?.data.find((document) => document.id === attachedDocumentId)?.filename ?? "已附加文档"}
                    <button
                      type="button"
                      aria-label="移除本次文档附件"
                      disabled={busy}
                      onClick={() => {
                        setAttachedDocumentId("")
                        if (scope === attachedDocumentId) setScope("")
                      }}
                    ><X size={16} aria-hidden="true" /></button>
                  </span>
                )}
            </div>
            <div className="da-composer-controls">
              <label className="da-toggle">
                <input
                  type="checkbox"
                  checked={useDocuments}
                  disabled={busy}
                  onChange={(event) => setUseDocuments(event.target.checked)}
                />
                使用我的知识库回答{" "}
                <small>
                  {documents.isError
                    ? "列表读取失败"
                    : `${documents.data?.count ?? 0} 份文档`}
                </small>
              </label>
              <div className="da-send-actions">
                {useDocuments && (
                  <button
                    type="button"
                    className="da-secondary"
                    disabled={busy}
                    onClick={handleRetrieve}
                  >
                    <Search size={18} aria-hidden="true" />
                    只检索原文
                  </button>
                )}
                <LoadingButton
                  className="da-primary"
                  type="submit"
                  loading={mutation.isPending}
                  disabled={busy}
                >
                  <Send aria-hidden="true" />
                  {mutation.isPending ? "正在回答…" : "发送问题"}
                </LoadingButton>
              </div>
            </div>
            {validationError && (
              <p role="alert" className="da-inline-error">
                {validationError}
              </p>
            )}
          </form>
        </section>
        <KnowledgeSourcePanel sources={sources} loading={retrieval.isPending} />
      </div>
      )}
    </>
  )
}
