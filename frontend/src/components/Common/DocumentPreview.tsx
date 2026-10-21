import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef } from "react"
import useAuth from "@/hooks/useAuth"
import { describeKnowledgeError, readDocument } from "@/lib/knowledge"

export function DocumentPreview({
  documentId,
  chunkId,
  onClose,
}: {
  documentId: string
  chunkId?: string
  onClose: () => void
}) {
  const { user } = useAuth()
  const panel = useRef<HTMLElement>(null)
  const target = useRef<HTMLElement>(null)
  const detail = useQuery({
    queryKey: ["document-detail", user?.id, documentId],
    queryFn: () => readDocument(documentId),
    enabled: Boolean(user),
    retry: false,
  })
  const data = detail.isError || detail.isFetching ? undefined : detail.data
  useEffect(() => {
    if (!detail.isPending && !detail.isFetching) {
      const element = target.current ?? panel.current
      element?.focus({ preventScroll: true })
      element?.scrollIntoView({ block: "nearest" })
    }
  }, [detail.isPending, detail.isFetching])
  return (
    <section
      ref={panel}
      tabIndex={-1}
      aria-label="文档片段预览"
      className="da-document-preview"
    >
      <header>
        <h2>{detail.data?.document.filename ?? "文档片段预览"}</h2>
        <button type="button" className="da-secondary" onClick={onClose}>
          关闭预览
        </button>
      </header>
      <p className="da-note">
        展示数据库中保存的分块，长文片段可能有重叠。行号对应导入时的原文。
      </p>
      {(detail.isPending || detail.isFetching) && (
        <p role="status">正在读取文档片段…</p>
      )}
      {detail.isError && (
        <div role="alert">
          <p>{describeKnowledgeError(detail.error)}</p>
          <button
            type="button"
            className="da-secondary"
            onClick={() => detail.refetch()}
          >
            重新加载片段
          </button>
        </div>
      )}
      {data &&
        chunkId &&
        !data.chunks.some((chunk) => chunk.id === chunkId) && (
          <p role="status" className="da-note">
            原来源片段已不可用，以下显示当前保存的文档片段。
          </p>
        )}
      {data?.chunks.map((chunk) => (
        <section
          key={chunk.id}
          ref={chunk.id === chunkId ? target : undefined}
          tabIndex={chunk.id === chunkId ? -1 : undefined}
          aria-label={`文档片段 ${chunk.position + 1}`}
          className={`da-preview-chunk ${chunk.id === chunkId ? "da-preview-selected" : ""}`}
        >
          <h3>{chunk.heading}</h3>
          <p className="da-note">
            片段 {chunk.position + 1} · 第 {chunk.line_start}～{chunk.line_end}{" "}
            行{chunk.id === chunkId ? " · 当前来源" : ""}
          </p>
          <pre>{chunk.content}</pre>
        </section>
      ))}
    </section>
  )
}
