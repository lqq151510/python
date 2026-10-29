import { Code2, FileText, Image as ImageIcon, NotebookPen } from "lucide-react"
import { Highlight } from "@/components/Workspace/Highlight"
import {
  formatDate,
  KIND_LABEL,
  type RankedItem,
  type WsKind,
} from "@/lib/workspace"

const KIND_ICON: Record<WsKind, typeof FileText> = {
  doc: FileText,
  note: NotebookPen,
  image: ImageIcon,
  code: Code2,
}

export function ResultsPanel({
  title,
  meta,
  rows,
  loading,
  warning,
  activeId,
  onSelect,
  sort,
  onSort,
  tokens,
}: {
  title: string
  meta: string
  rows: RankedItem[]
  loading: boolean
  warning: string | null
  activeId: string | null
  onSelect: (itemId: string) => void
  sort: "relevance" | "recent"
  onSort: (sort: "relevance" | "recent") => void
  tokens: string[]
}) {
  return (
    <section className="zm-results" aria-labelledby="zm-results-title">
      <header className="zm-results__head">
        <div>
          <h2 className="zm-results__title" id="zm-results-title">
            {title}
          </h2>
          <p className="zm-results__meta">{meta}</p>
        </div>
        <div>
          <label className="zm-visually-hidden" htmlFor="zm-sort-select">
            排序方式
          </label>
          <select
            className="zm-select"
            id="zm-sort-select"
            value={sort}
            onChange={(event) =>
              onSort(event.target.value === "recent" ? "recent" : "relevance")
            }
          >
            <option value="relevance">相关度</option>
            <option value="recent">最近更新</option>
          </select>
        </div>
      </header>

      {warning ? (
        <p className="zm-field__error" style={{ padding: "8px 16px" }}>
          {warning}
        </p>
      ) : null}

      <ul className="zm-results__list" aria-busy={loading}>
        {loading ? (
          <li style={{ padding: 16 }}>
            <div className="zm-skeleton zm-skeleton--title" />
            <div className="zm-skeleton" />
            <div className="zm-skeleton zm-skeleton--short" />
          </li>
        ) : rows.length === 0 ? (
          <li>
            <div className="zm-empty">
              <h3 className="zm-empty__title">没有匹配的资料</h3>
              <p className="zm-empty__text">
                换个关键词，或切换左侧的知识源范围。代码文件也可以直接按路径检索。
              </p>
            </div>
          </li>
        ) : (
          rows.map(({ item, snippet }) => {
            const Icon = KIND_ICON[item.kind]
            return (
              <li key={item.id}>
                <button
                  type="button"
                  className="zm-result"
                  aria-current={activeId === item.id ? "true" : undefined}
                  onClick={() => onSelect(item.id)}
                >
                  <span className="zm-result__top">
                    <span className={`zm-badge zm-badge--${item.kind}`}>
                      <Icon size={12} aria-hidden="true" />
                      {KIND_LABEL[item.kind]}
                    </span>
                    <span className="zm-result__title">
                      <Highlight text={item.title} tokens={tokens} />
                    </span>
                  </span>
                  <span className="zm-result__snippet">
                    <Highlight text={snippet} tokens={tokens} />
                  </span>
                  <span className="zm-result__meta">
                    {item.updated ? (
                      <time>{formatDate(item.updated)}</time>
                    ) : null}
                    <span>{item.source}</span>
                    {item.meta.map((entry) => (
                      <span key={entry}>{entry}</span>
                    ))}
                  </span>
                </button>
              </li>
            )
          })
        )}
      </ul>
    </section>
  )
}
