export function StatusBar({
  indexed,
  documents,
  files,
  mode,
  hint,
  branch,
}: {
  indexed: number
  documents: number
  files: number
  mode: "keyword" | "semantic"
  hint: string
  branch: string
}) {
  return (
    <footer className="zm-status" role="contentinfo">
      <span>
        <span className="zm-status__dot" aria-hidden="true" />
        已索引 {indexed} 条资料
      </span>
      <span>{documents} 篇文档</span>
      <span>{files} 个文件</span>
      <span>{mode === "keyword" ? "关键词检索" : "语义检索"}</span>
      <span className="zm-status__spacer" />
      <span>{hint}</span>
      <span>{branch}</span>
    </footer>
  )
}
