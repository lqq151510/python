import { Copy, FolderSearch, Plus } from "lucide-react"
import { Highlight } from "@/components/Workspace/Highlight"
import type { DocumentDetail } from "@/lib/knowledge"
import type { NoteRecord } from "@/lib/notes"
import {
  type CtxEntry,
  formatDate,
  KIND_LABEL,
  type WsItem,
} from "@/lib/workspace"

export function ReaderPanel({
  item,
  detail,
  note,
  imageUrl,
  loading,
  error,
  tokens,
  onAddContext,
  onFindInCode,
  onCopy,
}: {
  item: WsItem | null
  detail: DocumentDetail | null
  note: NoteRecord | null
  imageUrl: string | null
  loading: boolean
  error: string | null
  tokens: string[]
  onAddContext: (entry: CtxEntry) => void
  onFindInCode: (query: string) => void
  onCopy: (text: string) => void
}) {
  if (!item) {
    return (
      <section className="zm-reader" aria-label="阅读视图">
        <div className="zm-empty">
          <span className="zm-badge zm-badge--doc">阅读视图</span>
          <h3 className="zm-empty__title">选一条检索结果开始阅读</h3>
          <p className="zm-empty__text">
            左侧列表里的文档、笔记与代码都可以打开。打开后可以把片段引用到右侧工作台的上下文里，
            或跳到代码中的对应位置。
          </p>
        </div>
      </section>
    )
  }

  const excerpt =
    detail?.chunks[0]?.content.slice(0, 240) ??
    note?.body.slice(0, 240) ??
    item.summary

  return (
    <section className="zm-reader" aria-label="阅读视图">
      <div className="zm-reader__head">
        <div className="zm-reader__badges">
          <span className={`zm-badge zm-badge--${item.kind}`}>
            {KIND_LABEL[item.kind]}
          </span>
          {item.updated ? (
            <span className="zm-result__meta">
              <time>{formatDate(item.updated)}</time>
              <span>{item.source}</span>
            </span>
          ) : null}
        </div>
        <h2 className="zm-reader__title">
          <Highlight text={item.title} tokens={tokens} />
        </h2>
        {item.kind !== "image" && item.summary ? (
          <p className="zm-reader__summary">
            <Highlight text={item.summary} tokens={tokens} />
          </p>
        ) : null}

        <div className="zm-reader__actions">
          <button
            type="button"
            className="zm-btn zm-btn--primary zm-btn--small"
            onClick={() =>
              onAddContext({
                key: `${item.id}:head`,
                title: item.title,
                source: item.source,
                excerpt,
                documentId: item.kind === "doc" ? item.refId : undefined,
                path: item.kind === "code" ? item.path : undefined,
              })
            }
          >
            <Plus size={14} aria-hidden="true" />
            引用到上下文
          </button>
          {item.kind !== "image" ? (
            <button
              type="button"
              className="zm-btn zm-btn--ghost zm-btn--small"
              onClick={() => onFindInCode(item.title)}
            >
              <FolderSearch size={14} aria-hidden="true" />
              在代码中查找
            </button>
          ) : null}
          <button
            type="button"
            className="zm-btn zm-btn--ghost zm-btn--small"
            onClick={() =>
              onCopy(
                detail?.chunks.map((chunk) => chunk.content).join("\n\n") ??
                  note?.body ??
                  item.summary,
              )
            }
          >
            <Copy size={14} aria-hidden="true" />
            复制摘录
          </button>
        </div>
      </div>

      <div className="zm-reader__body">
        {loading ? (
          <>
            <div className="zm-skeleton zm-skeleton--title" />
            <div className="zm-skeleton" />
            <div className="zm-skeleton zm-skeleton--short" />
          </>
        ) : error ? (
          <p className="zm-field__error">{error}</p>
        ) : item.kind === "doc" ? (
          detail ? (
            <>
              <p className="zm-reader__summary">
                {detail.document.filename} · {detail.document.chunk_count}{" "}
                个片段 · {detail.chunks.length} 段已载入
              </p>
              {detail.chunks.map((chunk) => {
                const hasHit = tokens.some((token) =>
                  chunk.content.toLowerCase().includes(token),
                )
                return (
                  <article
                    key={chunk.id}
                    className={`zm-reader__chunk${hasHit ? " is-hit" : ""}`}
                  >
                    <div className="zm-reader__chunkhead">
                      <span>#{chunk.position + 1}</span>
                      <span>{chunk.heading || "正文"}</span>
                      <span>
                        第 {chunk.line_start}-{chunk.line_end} 行
                      </span>
                    </div>
                    <p className="zm-reader__chunkbody">
                      <Highlight text={chunk.content} tokens={tokens} />
                    </p>
                  </article>
                )
              })}
            </>
          ) : (
            <p className="zm-empty__text">文档内容不可用。</p>
          )
        ) : item.kind === "note" ? (
          note ? (
            <>
              <p className="zm-reader__chunkhead">
                {formatDate(note.updated_at || note.created_at)} · 学习笔记
              </p>
              {note.body
                .split(/\n{2,}/)
                .filter((paragraph) => paragraph.trim())
                .map((paragraph) => (
                  <p className="zm-reader__paragraph" key={paragraph}>
                    <Highlight text={paragraph} tokens={tokens} />
                  </p>
                ))}
            </>
          ) : (
            <p className="zm-empty__text">笔记内容不可用。</p>
          )
        ) : item.kind === "image" ? (
          imageUrl ? (
            <>
              <img
                className="zm-reader__image"
                src={imageUrl}
                alt={item.title}
                width={720}
                height={480}
              />
              <p className="zm-reader__chunkhead">
                {item.summary} · 图片保存在账号隔离的图片库里
              </p>
            </>
          ) : (
            <p className="zm-empty__text">正在加载图片…</p>
          )
        ) : (
          <p className="zm-empty__text">
            代码文件在右侧工作台打开；这里只显示文件元信息。
          </p>
        )}
      </div>
    </section>
  )
}
