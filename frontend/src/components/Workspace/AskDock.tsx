import { Send, X } from "lucide-react"
import type { KnowledgeSource } from "@/lib/knowledge"
import type { AskTurn, CtxEntry } from "@/lib/workspace"

export function AskDock({
  context,
  onRemove,
  onClear,
  turns,
  question,
  onQuestion,
  onAsk,
  busy,
  error,
  onOpenSource,
}: {
  context: CtxEntry[]
  onRemove: (key: string) => void
  onClear: () => void
  turns: AskTurn[]
  question: string
  onQuestion: (value: string) => void
  onAsk: () => void
  busy: boolean
  error: string | null
  onOpenSource: (source: KnowledgeSource) => void
}) {
  return (
    <section className="zm-dock" aria-label="提问区">
      <div className="zm-dock__ctx">
        <header className="zm-ctx__head">
          <h3 className="zm-ctx__title">上下文</h3>
          <span className="zm-ctx__count">{context.length} 条</span>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            className="zm-btn zm-btn--ghost zm-btn--small"
            onClick={onClear}
            disabled={context.length === 0}
          >
            清空
          </button>
        </header>
        <ul className="zm-ctx__list">
          {context.length === 0 ? (
            <li
              className="zm-ctx__item"
              style={{ display: "block", padding: "10px 12px" }}
            >
              <p className="zm-empty__text" style={{ fontSize: 12 }}>
                还没有引用任何片段。在阅读视图里点「引用到上下文」，被引用的段落会汇总到这里。
              </p>
            </li>
          ) : (
            context.map((entry) => (
              <li className="zm-ctx__item" key={entry.key}>
                <span className="zm-ctx__name" title={entry.title}>
                  {entry.title}
                </span>
                <button
                  type="button"
                  className="zm-ctx__remove"
                  aria-label={`移除 ${entry.title}`}
                  onClick={() => onRemove(entry.key)}
                >
                  <X size={12} aria-hidden="true" />
                </button>
                <span className="zm-ctx__src">{entry.source}</span>
                <span className="zm-ctx__excerpt">{entry.excerpt}</span>
              </li>
            ))
          )}
        </ul>
      </div>

      <div className="zm-dock__ask">
        <div className="zm-ask__thread" role="log" aria-live="polite">
          {turns.length === 0 ? (
            <div className="zm-empty zm-ask__empty">
              <h3 className="zm-empty__title">基于当前上下文提问</h3>
              <p className="zm-empty__text">
                未引用上下文时只检索知识库。引用片段后，提问会带上这些摘录；只引用一篇文档时还会限定检索范围。回答里的来源可以点开核对。
              </p>
            </div>
          ) : (
            turns.map((turn) => (
              <article className="zm-turn" key={turn.id}>
                <p className="zm-turn__q">{turn.question}</p>
                {turn.pending ? (
                  <p className="zm-turn__note">正在检索并生成回答…</p>
                ) : turn.error ? (
                  <p className="zm-field__error">{turn.error}</p>
                ) : (
                  <p className="zm-turn__a">{turn.answer}</p>
                )}
                {turn.sources && turn.sources.length > 0 ? (
                  <div className="zm-turn__sources">
                    {turn.sources.map((source) => (
                      <button
                        key={`${turn.id}-${source.chunk_id}`}
                        type="button"
                        className="zm-turn__source"
                        onClick={() => onOpenSource(source)}
                      >
                        <span>
                          [{source.citation}] {source.filename}
                        </span>
                        <span>
                          {source.heading || "正文"} · 第 {source.line_start}-
                          {source.line_end} 行
                        </span>
                      </button>
                    ))}
                  </div>
                ) : null}
              </article>
            ))
          )}
        </div>

        <form
          className="zm-ask__composer"
          onSubmit={(event) => {
            event.preventDefault()
            onAsk()
          }}
        >
          <label className="zm-visually-hidden" htmlFor="zm-ask-input">
            向知识库提问
          </label>
          <textarea
            className="zm-ask__input"
            id="zm-ask-input"
            rows={2}
            placeholder="基于当前上下文提问，Enter 发送"
            value={question}
            onChange={(event) => onQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault()
                onAsk()
              }
            }}
          />
          <div className="zm-ask__row">
            <span className="zm-ask__hint">
              {context.length
                ? `已引用 ${context.length} 条片段，提问会带上这些摘录`
                : "未引用上下文时将只检索知识库"}
            </span>
            <button
              className="zm-btn zm-btn--primary"
              type="submit"
              disabled={busy}
            >
              <Send size={14} aria-hidden="true" />
              {busy ? "发送中…" : "发送"}
            </button>
          </div>
          {error ? <p className="zm-field__error">{error}</p> : null}
        </form>
      </div>
    </section>
  )
}
