import type { KnowledgeSource } from "@/lib/knowledge"

export function KnowledgeSources({ sources }: { sources: KnowledgeSource[] }) {
  return (
    <div className="da-result-list">
      {sources.map((source) => (
        <article key={source.chunk_id} className="da-result-item">
          <p className="da-result-name">
            [{source.citation}] {source.filename}
          </p>
          <p className="da-note">
            {source.heading} · 第 {source.line_start}～{source.line_end} 行
          </p>
          <details>
            <summary>查看原文片段</summary>
            <pre className="da-result-content">{source.content}</pre>
          </details>
        </article>
      ))}
    </div>
  )
}
