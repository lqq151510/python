import { Link } from "@tanstack/react-router"
import { Moon, PanelLeft, Plus, Search, Sun, X } from "lucide-react"
import type { WsItem, WsScope } from "@/lib/workspace"
import { KIND_LABEL } from "@/lib/workspace"

export function WorkspaceHeader({
  query,
  onQueryChange,
  scope,
  onScopeChange,
  counts,
  mode,
  onToggleMode,
  theme,
  onToggleTheme,
  onNewNote,
  sourcesOpen,
  onToggleSources,
  suggestions,
  onPickSuggestion,
  pane,
  onPaneChange,
  suggestOpen,
  onSuggestOpenChange,
}: {
  query: string
  onQueryChange: (value: string) => void
  scope: WsScope
  onScopeChange: (value: WsScope) => void
  counts: Record<WsScope, number>
  mode: "keyword" | "semantic"
  onToggleMode: () => void
  theme: "light" | "dark"
  onToggleTheme: () => void
  onNewNote: () => void
  sourcesOpen: boolean
  onToggleSources: () => void
  suggestions: WsItem[]
  onPickSuggestion: (item: WsItem) => void
  pane: "knowledge" | "code"
  onPaneChange: (pane: "knowledge" | "code") => void
  suggestOpen: boolean
  onSuggestOpenChange: (open: boolean) => void
}) {
  const scopes: Array<{ key: WsScope; label: string }> = [
    { key: "all", label: "全部" },
    { key: "doc", label: "文档" },
    { key: "note", label: "笔记" },
    { key: "image", label: "图片" },
    { key: "code", label: "代码" },
  ]

  return (
    <header className="zm-topbar">
      <div className="zm-topbar__group">
        <button
          className="zm-icon-btn"
          type="button"
          aria-expanded={sourcesOpen}
          aria-controls="zm-sources"
          onClick={onToggleSources}
          title="知识源与过滤"
        >
          <PanelLeft size={18} aria-hidden="true" />
          <span className="zm-visually-hidden">打开知识源与过滤</span>
        </button>

        <Link to="/workspace" className="zm-brand">
          <span className="zm-brand__mark" aria-hidden="true">
            知
          </span>
          <span className="zm-brand__name">知码</span>
        </Link>

        <div className="zm-search">
          <Search size={16} aria-hidden="true" />
          <label className="zm-visually-hidden" htmlFor="zm-search-input">
            检索文档、笔记、图片与代码
          </label>
          <input
            className="zm-search__input"
            id="zm-search-input"
            type="search"
            autoComplete="off"
            spellCheck={false}
            placeholder="检索文档、笔记与代码"
            value={query}
            onFocus={() => onSuggestOpenChange(true)}
            onBlur={(event) => {
              if (
                !event.currentTarget.parentElement?.contains(
                  event.relatedTarget,
                )
              ) {
                onSuggestOpenChange(false)
              }
            }}
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") onQueryChange("")
            }}
          />
          {query ? (
            <button
              className="zm-search__clear"
              type="button"
              onClick={() => onQueryChange("")}
              onBlur={(event) => {
                if (
                  !event.currentTarget.parentElement?.contains(
                    event.relatedTarget,
                  )
                ) {
                  onSuggestOpenChange(false)
                }
              }}
            >
              <X size={14} aria-hidden="true" />
              <span className="zm-visually-hidden">清除检索词</span>
            </button>
          ) : null}
          <kbd className="zm-search__kbd">⌘K</kbd>
          {suggestOpen && query && suggestions.length > 0 ? (
            <ul className="zm-suggest" aria-label="检索建议">
              {suggestions.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="zm-suggest__item"
                    onClick={() => onPickSuggestion(item)}
                    onBlur={(event) => {
                      if (
                        !event.currentTarget
                          .closest(".zm-search")
                          ?.contains(event.relatedTarget)
                      ) {
                        onSuggestOpenChange(false)
                      }
                    }}
                  >
                    <span className="zm-suggest__title">{item.title}</span>
                    <span className="zm-suggest__src">
                      {KIND_LABEL[item.kind]}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        <fieldset className="zm-scope">
          <legend className="zm-visually-hidden">检索范围</legend>
          {scopes.map((item) => (
            <button
              key={item.key}
              className="zm-chip"
              type="button"
              aria-pressed={scope === item.key}
              title={`${item.label}（${counts[item.key]}）`}
              onClick={() => onScopeChange(item.key)}
            >
              {item.label}
              {item.key !== "all" ? ` ${counts[item.key]}` : ""}
            </button>
          ))}
        </fieldset>
      </div>

      <div className="zm-topbar__group zm-topbar__group--end">
        <div className="zm-seg">
          <button
            className="zm-seg__btn"
            type="button"
            aria-pressed={pane === "knowledge"}
            onClick={() => onPaneChange("knowledge")}
          >
            知识
          </button>
          <button
            className="zm-seg__btn"
            type="button"
            aria-pressed={pane === "code"}
            onClick={() => onPaneChange("code")}
          >
            代码
          </button>
        </div>
        <button
          className="zm-btn zm-btn--ghost"
          type="button"
          aria-pressed={mode === "semantic"}
          title="关键词检索由本地 BM25 完成；语义检索需要本机向量服务"
          onClick={onToggleMode}
        >
          {mode === "keyword" ? "关键词检索" : "语义检索"}
        </button>
        <button
          className="zm-btn zm-btn--ghost"
          type="button"
          onClick={onNewNote}
        >
          <Plus size={14} aria-hidden="true" />
          新建速记
        </button>
        <button
          className="zm-icon-btn"
          type="button"
          onClick={onToggleTheme}
          aria-label={theme === "light" ? "切换到深色主题" : "切换到浅色主题"}
        >
          {theme === "light" ? (
            <Moon size={18} aria-hidden="true" />
          ) : (
            <Sun size={18} aria-hidden="true" />
          )}
        </button>
        <Link className="zm-icon-btn" to="/chat" title="返回问答工作台">
          <span aria-hidden="true">↩</span>
          <span className="zm-visually-hidden">返回问答工作台</span>
        </Link>
      </div>
    </header>
  )
}
