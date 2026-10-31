import { Link } from "@tanstack/react-router"
import {
  Code2,
  FileText,
  Image as ImageIcon,
  Layers,
  NotebookPen,
} from "lucide-react"
import type { NoteRecord } from "@/lib/notes"
import { formatDate, type WsScope } from "@/lib/workspace"

const SOURCE_ROWS: Array<{
  key: WsScope
  label: string
  icon: typeof Layers
}> = [
  { key: "all", label: "全部资料", icon: Layers },
  { key: "doc", label: "文档", icon: FileText },
  { key: "note", label: "我的笔记", icon: NotebookPen },
  { key: "image", label: "图片", icon: ImageIcon },
  { key: "code", label: "代码仓库", icon: Code2 },
]

export function SourcesPanel({
  counts,
  scope,
  onScope,
  notes,
  onOpenNote,
  open,
}: {
  counts: Record<WsScope, number>
  scope: WsScope
  onScope: (scope: WsScope) => void
  notes: NoteRecord[]
  onOpenNote: (id: string) => void
  open: boolean
}) {
  return (
    <aside
      className={`zm-sources${open ? " is-open" : ""}`}
      id="zm-sources"
      aria-label="知识源与过滤"
    >
      <section>
        <h2 className="zm-sources__title">知识源</h2>
        <ul className="zm-srclist">
          {SOURCE_ROWS.map((row) => {
            const Icon = row.icon
            return (
              <li key={row.key}>
                <button
                  type="button"
                  className="zm-srclist__btn"
                  aria-pressed={scope === row.key}
                  onClick={() => onScope(row.key)}
                >
                  <Icon size={15} aria-hidden="true" />
                  {row.label}
                  <span className="zm-srclist__count">{counts[row.key]}</span>
                </button>
              </li>
            )
          })}
        </ul>
      </section>

      <section>
        <h2 className="zm-sources__title">我的速记</h2>
        {notes.length === 0 ? (
          <p className="zm-sources__empty">
            还没有速记。复盘中随手记一条，之后就能用标题检索到它。
          </p>
        ) : (
          <div className="zm-notes">
            {notes.slice(0, 6).map((note) => (
              <button
                key={note.id}
                type="button"
                className="zm-notes__item"
                onClick={() => onOpenNote(note.id)}
              >
                {note.title}
                <span className="zm-notes__meta">
                  {formatDate(note.updated_at || note.created_at)}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section>
        <Link to="/knowledge" className="zm-btn zm-btn--ghost">
          管理知识库
        </Link>
        <h2 className="zm-sources__title">说明</h2>
        <p className="zm-sources__empty">
          资料全部来自你自己的知识库；速记保存到「学习笔记」，代码文件来自账号隔离沙箱。
        </p>
      </section>
    </aside>
  )
}
