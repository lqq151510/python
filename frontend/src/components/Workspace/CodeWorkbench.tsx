import { ChevronDown, FileCode2, FolderOpen, Search, X } from "lucide-react"
import { type ReactNode, useEffect, useRef } from "react"
import { Highlight } from "@/components/Workspace/Highlight"
import type { CodeWorkspaceFile } from "@/lib/code"
import { tokensOf } from "@/lib/workspace"

const KEYWORDS = new Set([
  "const",
  "let",
  "var",
  "function",
  "return",
  "if",
  "else",
  "for",
  "while",
  "class",
  "import",
  "from",
  "export",
  "default",
  "async",
  "await",
  "def",
  "self",
  "None",
  "True",
  "False",
  "with",
  "as",
  "try",
  "except",
  "raise",
  "new",
  "this",
  "type",
  "interface",
  "public",
  "private",
  "final",
  "void",
  "int",
  "str",
  "bool",
  "float",
  "true",
  "false",
  "null",
  "undefined",
])

const TOKEN_PATTERN =
  /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_$][\w$]*)/g

function commentStart(line: string): number {
  const trimmed = line.trimStart()
  if (trimmed.startsWith("//") || trimmed.startsWith("#")) return 0
  if (trimmed.startsWith("<!--")) return 0
  return -1
}

function renderCodeLine(line: string): ReactNode[] {
  TOKEN_PATTERN.lastIndex = 0
  if (commentStart(line) === 0) {
    return [
      <span className="zm-tk-com" key="comment">
        {line}
      </span>,
    ]
  }
  const nodes: ReactNode[] = []
  let cursor = 0
  let matches = TOKEN_PATTERN.exec(line)
  while (matches) {
    if (matches.index > cursor) {
      nodes.push(line.slice(cursor, matches.index))
    }
    const [text, string, number, word] = matches
    if (string) {
      nodes.push(
        <span className="zm-tk-str" key={`s-${matches.index}`}>
          {text}
        </span>,
      )
    } else if (number) {
      nodes.push(
        <span className="zm-tk-num" key={`n-${matches.index}`}>
          {text}
        </span>,
      )
    } else if (word && KEYWORDS.has(word)) {
      nodes.push(
        <span className="zm-tk-kw" key={`k-${matches.index}`}>
          {text}
        </span>,
      )
    } else if (word) {
      const isCall = line.slice(matches.index + text.length).startsWith("(")
      nodes.push(
        isCall ? (
          <span className="zm-tk-fn" key={`f-${matches.index}`}>
            {text}
          </span>
        ) : (
          text
        ),
      )
    } else {
      nodes.push(text)
    }
    cursor = matches.index + text.length
    matches = TOKEN_PATTERN.exec(line)
  }
  if (cursor < line.length) nodes.push(line.slice(cursor))
  if (!nodes.length) nodes.push(" ")
  return nodes
}

interface TreeDir {
  name: string
  dirs: TreeDir[]
  files: CodeWorkspaceFile[]
}

function buildTree(files: CodeWorkspaceFile[]): TreeDir {
  const root: TreeDir = { name: "", dirs: [], files: [] }
  for (const file of files) {
    const parts = file.path.split("/")
    let node = root
    for (const dir of parts.slice(0, -1)) {
      let next = node.dirs.find((entry) => entry.name === dir)
      if (!next) {
        next = { name: dir, dirs: [], files: [] }
        node.dirs.push(next)
      }
      node = next
    }
    node.files.push(file)
  }
  return root
}

export function CodeWorkbench({
  files,
  openPaths,
  activePath,
  contents,
  loadingPath,
  query,
  onOpen,
  onClose,
  onFindNotes,
  onFindCode,
  children,
}: {
  files: CodeWorkspaceFile[]
  openPaths: string[]
  activePath: string | null
  contents: Record<string, string | undefined>
  loadingPath: string | null
  query: string
  onOpen: (path: string) => void
  onClose: (path: string) => void
  onFindNotes: () => void
  onFindCode: () => void
  children?: ReactNode
}) {
  const tree = buildTree(files)
  const tokens = tokensOf(query)
  const content = activePath ? contents[activePath] : undefined
  const codeRef = useRef<HTMLElement | null>(null)

  // 命中词、文件或内容变化后，把第一处命中滚到视野中间。
  useEffect(() => {
    const container = codeRef.current
    if (!container || !activePath || content === undefined || !query) return
    const firstHit = container.querySelector<HTMLElement>(
      ".zm-code__line.is-hit",
    )
    if (firstHit) {
      container.scrollTop = Math.max(
        0,
        firstHit.offsetTop - container.clientHeight / 2,
      )
    }
  }, [activePath, content, query])

  const renderDir = (dir: TreeDir, depth: number): ReactNode => (
    <div key={`dir-${dir.name}-${depth}`}>
      {dir.name ? (
        <div className="zm-tree__dir" style={{ paddingLeft: 6 + depth * 12 }}>
          <ChevronDown size={12} aria-hidden="true" />
          {dir.name}
        </div>
      ) : null}
      {dir.files
        .slice()
        .sort((left, right) => left.path.localeCompare(right.path))
        .map((file) => (
          <button
            key={file.path}
            type="button"
            className="zm-tree__row"
            aria-current={activePath === file.path ? "true" : undefined}
            style={{ paddingLeft: 6 + (depth + (dir.name ? 1 : 0)) * 12 }}
            onClick={() => onOpen(file.path)}
            title={file.path}
          >
            <FileCode2 size={13} aria-hidden="true" />
            <span className="zm-tree__name">{file.path.split("/").pop()}</span>
          </button>
        ))}
      {dir.dirs.map((child) => renderDir(child, depth + 1))}
    </div>
  )

  return (
    <section className="zm-work" aria-label="代码工作台">
      <header className="zm-work__head">
        <span className="zm-work__branch">
          <FolderOpen size={13} aria-hidden="true" />
          沙箱工作区
        </span>
        <span className="zm-work__branch">
          <ChevronDown size={12} aria-hidden="true" />
          workspace
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="zm-btn zm-btn--ghost zm-btn--small"
          onClick={onFindNotes}
        >
          <Search size={13} aria-hidden="true" />
          查找相关笔记
        </button>
        <button
          type="button"
          className="zm-btn zm-btn--ghost zm-btn--small"
          onClick={onFindCode}
        >
          <FileCode2 size={13} aria-hidden="true" />
          在代码中查找
        </button>
      </header>

      <div className="zm-work__tabs" role="tablist" aria-label="已打开的文件">
        {openPaths.length === 0 ? (
          <span className="zm-work__empty-tab">未打开文件</span>
        ) : (
          openPaths.map((path) => (
            <div
              key={path}
              className="zm-tab"
              aria-current={activePath === path ? "true" : undefined}
            >
              <button
                type="button"
                className="zm-tab__open"
                title={path}
                onClick={() => onOpen(path)}
              >
                <span className="zm-tab__name">{path.split("/").pop()}</span>
              </button>
              <button
                type="button"
                className="zm-tab__close"
                aria-label={`关闭 ${path}`}
                onClick={() => onClose(path)}
              >
                <X size={12} aria-hidden="true" />
              </button>
            </div>
          ))
        )}
      </div>

      <div className="zm-work__body">
        <nav className="zm-tree" aria-label="仓库文件">
          {files.length === 0 ? (
            <p className="zm-sources__empty" style={{ padding: "8px" }}>
              沙箱里还没有文件。去「编程工作台」上传或新建一个。
            </p>
          ) : (
            renderDir(tree, 0)
          )}
        </nav>

        <section className="zm-code" aria-label="代码查看器" ref={codeRef}>
          {activePath ? (
            loadingPath === activePath ? (
              <div style={{ padding: 16 }}>
                <div className="zm-skeleton zm-skeleton--title" />
                <div className="zm-skeleton" />
                <div className="zm-skeleton zm-skeleton--short" />
              </div>
            ) : content === undefined ? (
              <p className="zm-sources__empty" style={{ padding: 16 }}>
                文件内容不可用。
              </p>
            ) : (
              content.split("\n").map((line, index) => {
                const lower = line.toLowerCase()
                const hit = tokens.some((token) => lower.includes(token))
                return (
                  <div
                    key={`${activePath}-${index}`}
                    className={`zm-code__line${hit ? " is-hit" : ""}`}
                  >
                    <span className="zm-code__num">{index + 1}</span>
                    <span className="zm-code__text">
                      {tokens.length ? (
                        <Highlight
                          text={line || " "}
                          tokens={tokens}
                          className="zm-mark"
                        />
                      ) : (
                        renderCodeLine(line)
                      )}
                    </span>
                  </div>
                )
              })
            )
          ) : (
            <div className="zm-empty" style={{ padding: "40px 24px" }}>
              <h3 className="zm-empty__title">还没有打开文件</h3>
              <p className="zm-empty__text">
                从左侧文件树选一个文件，或在检索结果里点开一个文档后再引用上下文。
              </p>
            </div>
          )}
        </section>
      </div>

      {children}
    </section>
  )
}
