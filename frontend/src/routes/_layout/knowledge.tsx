import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { BookOpen, Search, Upload } from "lucide-react"
import { type FormEvent, useRef, useState } from "react"
import { DevAssistHeader } from "@/components/Common/DevAssistLayout"
import { DocumentPreview } from "@/components/Common/DocumentPreview"
import { DocumentScope } from "@/components/Common/DocumentScope"
import { KnowledgeSources } from "@/components/Common/KnowledgeSources"
import {
  RetrievalMethod,
  RetrievalStatus,
} from "@/components/Common/RetrievalMethod"
import { Button } from "@/components/ui/button"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import {
  type RetrievalMethod as RetrievalMode,
  deleteDocument,
  describeKnowledgeError,
  listDocuments,
  searchDocuments,
  uploadDocument,
  validDocumentId,
} from "@/lib/knowledge"

export const Route = createFileRoute("/_layout/knowledge")({
  component: Knowledge,
  head: () => ({ meta: [{ title: "文档知识库 - DevAssist" }] }),
  validateSearch: (
    search: Record<string, unknown>,
  ): { document?: string; chunk?: string } => ({
    document: validDocumentId(search.document),
    chunk: validDocumentId(search.chunk),
  }),
})

function Knowledge() {
  const preview = Route.useSearch()
  const navigate = Route.useNavigate()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [query, setQuery] = useState("")
  const [scope, setScope] = useState("")
  const [method, setMethod] = useState<RetrievalMode>("bm25")
  const [notice, setNotice] = useState("")
  const [inputError, setInputError] = useState("")
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const uploadLock = useRef(false)
  const searchLock = useRef(false)
  const deleteLock = useRef(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const documents = useQuery({
    queryKey: ["documents", user?.id],
    queryFn: listDocuments,
    enabled: Boolean(user),
  })
  const upload = useMutation({
    mutationFn: uploadDocument,
    retry: false,
    onSuccess: async (result) => {
      setNotice(
        result.duplicate
          ? "内容已存在，已保留原文档，没有重复导入。"
          : "文档导入成功，可以检索了。",
      )
      setFile(null)
      search.reset()
      if (fileInput.current) fileInput.current.value = ""
      await queryClient.invalidateQueries({ queryKey: ["documents", user?.id] })
    },
  })
  const search = useMutation({
    mutationFn: (input: { query: string; documentId?: string }) =>
      searchDocuments(input.query, input.documentId, method),
    retry: false,
  })
  const remove = useMutation({
    mutationFn: deleteDocument,
    retry: false,
    onSuccess: async (_, id) => {
      setDeleteId(null)
      search.reset()
      if (scope === id) setScope("")
      if (preview.document === id) await navigate({ search: {} })
      queryClient.removeQueries({ queryKey: ["document-detail", user?.id, id] })
      setNotice("文档及其片段已删除。")
      await queryClient.invalidateQueries({ queryKey: ["documents", user?.id] })
    },
  })

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (uploadLock.current) return
    setInputError("")
    setNotice("")
    if (!file || !/\.(md|txt)$/i.test(file.name)) {
      setInputError("请选择 .md 或 .txt 文件。")
      return
    }
    if (file.size > 256 * 1024) {
      setInputError("单个文件不能超过 256 KiB。")
      return
    }
    uploadLock.current = true
    try {
      await upload.mutateAsync(file)
    } catch {
      // Render the safe API error below.
    } finally {
      uploadLock.current = false
    }
  }

  async function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (searchLock.current) return
    setInputError("")
    const trimmed = query.trim()
    if (!trimmed || Array.from(trimmed).length > 1000) {
      setInputError("请输入 1～1000 个字符的检索内容。")
      return
    }
    searchLock.current = true
    try {
      await search.mutateAsync({
        query: trimmed,
        documentId: scope || undefined,
      })
    } catch {
      // The mutation retains the safe error state.
    } finally {
      searchLock.current = false
    }
  }

  async function confirmDelete(id: string) {
    if (deleteLock.current) return
    deleteLock.current = true
    setNotice("")
    try {
      await remove.mutateAsync(id)
    } catch {
      // Keep the confirmation open so the user can retry.
    } finally {
      deleteLock.current = false
    }
  }

  const failure =
    documents.error || upload.error || search.error || remove.error
  return (
    <>
      <DevAssistHeader knowledge />
      <div className="da-knowledge">
        <div className="da-knowledge-intro">
          <p>导入项目说明或排障笔记，让问题有原文可查。检索无需模型密钥。</p>
          <Link to="/chat" className="da-text-button">
            去问答工作台
          </Link>
        </div>
        <form onSubmit={handleUpload} className="da-upload-form">
          <label htmlFor="knowledge-file">导入文档</label>
          <input
            ref={fileInput}
            id="knowledge-file"
            type="file"
            accept=".md,.txt"
            disabled={upload.isPending}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              setInputError("")
              upload.reset()
            }}
          />
          <div className="da-form-actions">
            <p>UTF-8 · 单文件最多 256 KiB · 每人最多 20 份</p>
            <LoadingButton
              className="da-primary"
              loading={upload.isPending}
              type="submit"
            >
              <Upload aria-hidden="true" />
              {upload.isPending ? "正在导入…" : "上传文档"}
            </LoadingButton>
          </div>
        </form>
        {inputError && (
          <p role="alert" className="da-inline-error">
            {inputError}
          </p>
        )}
        {failure && (
          <p role="alert" className="da-inline-error">
            {describeKnowledgeError(failure)}
          </p>
        )}
        {notice && (
          <p role="status" className="da-notice">
            {notice}
          </p>
        )}
        <section aria-label="已导入文档" className="da-document-library">
          <div className="da-library-heading">
            <h2>
              已导入文档 <span>{documents.data?.count ?? 0} / 20</span>
            </h2>
            <small>保存在本地数据库</small>
          </div>
          {documents.isPending && <p className="da-note">正在读取文档…</p>}
          {documents.data?.data.length === 0 && (
            <div className="da-library-empty">
              <BookOpen size={30} aria-hidden="true" />
              <p>还没有文档，先上传一份项目说明。</p>
            </div>
          )}
          <div className="da-document-list">
            {documents.data?.data.map((doc) => (
              <div key={doc.id} className="da-document-row">
                <div>
                  <strong>{doc.filename}</strong>
                  <small>
                    {doc.chunk_count} 个片段 ·{" "}
                    {(doc.size_bytes / 1024).toFixed(1)} KiB
                  </small>
                </div>
                <div className="da-document-actions">
                  <Link
                    to="/knowledge"
                    search={{ document: doc.id }}
                    className="da-secondary"
                  >
                    预览 {doc.filename}
                  </Link>
                  {deleteId === doc.id ? (
                    <div className="da-delete-confirm">
                      <span>同时删除所有片段？</span>
                      <LoadingButton
                        className="da-danger"
                        variant="destructive"
                        loading={remove.isPending}
                        onClick={() => confirmDelete(doc.id)}
                      >
                        确认删除
                      </LoadingButton>
                      <Button
                        className="da-secondary"
                        variant="outline"
                        disabled={remove.isPending}
                        onClick={() => setDeleteId(null)}
                      >
                        取消
                      </Button>
                    </div>
                  ) : (
                    <Button
                      className="da-secondary"
                      variant="outline"
                      disabled={remove.isPending}
                      onClick={() => {
                        remove.reset()
                        setDeleteId(doc.id)
                      }}
                    >
                      删除 {doc.filename}
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
        {preview.document && (
          <DocumentPreview
            key={`${preview.document}:${preview.chunk ?? ""}`}
            documentId={preview.document}
            chunkId={preview.chunk}
            onClose={() => navigate({ search: {} })}
          />
        )}
        <form onSubmit={handleSearch} className="da-library-search">
          <h2>在知识库中查找</h2>
          <DocumentScope
            value={scope}
            documents={documents.data?.data ?? []}
            disabled={search.isPending || remove.isPending}
            onChange={(value) => {
              setScope(value)
              search.reset()
              setInputError("")
            }}
          />
          <RetrievalMethod
            value={method}
            disabled={search.isPending}
            onChange={(value) => {
              setMethod(value)
              search.reset()
            }}
          />
          <label htmlFor="knowledge-query" className="da-note">
            检索内容
          </label>
          <div className="da-search-controls">
            <input
              id="knowledge-query"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value)
                setInputError("")
              }}
              disabled={search.isPending}
              placeholder="例如：读取超时是否重试"
            />
            <LoadingButton
              className="da-secondary"
              type="submit"
              loading={search.isPending}
            >
              <Search aria-hidden="true" />
              {search.isPending ? "正在检索…" : "检索文档"}
            </LoadingButton>
          </div>
          <p className="da-note">
            关键词适合精确词语；语义检索查找相近含义，混合检索结合两者。
          </p>
        </form>
        {search.data && (
          <section aria-label="检索结果" className="da-search-results">
            <h2>检索结果：{search.data.query}</h2>
            <RetrievalStatus
              method={search.data.method}
              warning={search.data.warning}
            />
            {search.data.sources.length ? (
              <KnowledgeSources sources={search.data.sources} />
            ) : (
              <div className="da-library-empty">
                <Search size={28} aria-hidden="true" />
                <p>没有匹配的片段，请尝试文档中的关键词。</p>
              </div>
            )}
            <Link
              to="/chat"
              search={{
                question: search.data.query,
                method,
                document: search.data.document_id ?? undefined,
              }}
              className="da-text-button"
            >
              带着这个问题去问答
            </Link>
          </section>
        )}
      </div>
    </>
  )
}
