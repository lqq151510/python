import { Link } from "@tanstack/react-router"
import { BookOpen, ChevronRight } from "lucide-react"
import { useState } from "react"
import type { KnowledgeSource } from "@/lib/knowledge"

export function KnowledgeSourcePanel({
  sources,
  loading = false,
}: {
  sources: KnowledgeSource[]
  loading?: boolean
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const active =
    sources.find((source) => source.chunk_id === selectedId) ?? sources[0]
  return (
    <aside className="da-sources" aria-label="本次来源">
      <header>
        <h2>本次来源</h2>
        <p>{sources.length} 个片段</p>
      </header>
      {active ? (
        <>
          <div className="da-source-list">
            {sources.map((source) => (
              <button
                type="button"
                key={source.chunk_id}
                className="da-source-row"
                aria-pressed={source === active}
                onClick={() => setSelectedId(source.chunk_id)}
              >
                <span>
                  <strong>
                    [{source.citation}] {source.filename}
                  </strong>
                  <small>
                    {source.heading} · 第 {source.line_start}～{source.line_end}{" "}
                    行
                  </small>
                </span>
                <ChevronRight size={19} aria-hidden="true" />
              </button>
            ))}
          </div>
          <section className="da-excerpt">
            <div>
              <h3>原文 · {active.heading}</h3>
              <Link
                to="/knowledge"
                search={{
                  document: active.document_id,
                  chunk: active.chunk_id,
                }}
              >
                打开来源文档
              </Link>
            </div>
            <p className="da-source-range">
              {active.filename} · 第 {active.line_start}～{active.line_end} 行
            </p>
            <section aria-label={`原文片段 ${active.citation}`}>
              <pre>{active.content}</pre>
            </section>
            <p className="da-note">来源为检索片段，请结合原文核对。</p>
          </section>
        </>
      ) : (
        <div className="da-sources-empty">
          <BookOpen size={28} aria-hidden="true" />
          <p>{loading ? "正在检索相关原文…" : "回答的依据会显示在这里"}</p>
          <small>勾选知识库后，可以只检索原文，无需模型密钥。</small>
        </div>
      )}
    </aside>
  )
}
