import type { RetrievalMethod as Method } from "@/lib/knowledge"

const labels: Record<Method, string> = {
  bm25: "关键词检索",
  vector: "语义检索",
  hybrid: "混合检索",
}

export function RetrievalMethod({
  value,
  disabled,
  onChange,
}: {
  value: Method
  disabled?: boolean
  onChange: (method: Method) => void
}) {
  return (
    <label className="da-document-scope">
      <span>检索方式</span>
      <select
        aria-label="检索方式"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as Method)}
      >
        {Object.entries(labels).map(([method, label]) => (
          <option key={method} value={method}>
            {label}
          </option>
        ))}
      </select>
    </label>
  )
}

export function RetrievalStatus({
  method,
  warning,
}: {
  method?: string | null
  warning?: string | null
}) {
  if (!method) return null
  const reason =
    warning === "vector_capacity"
      ? "当前范围超过 256 个片段，请缩小文档范围后使用语义检索。"
      : warning === "vector_timeout"
        ? "语义检索超时，本次已改用关键词检索。"
        : "语义检索暂不可用，本次已改用关键词检索。"
  return (
    <p className="da-note" role="status">
      本次使用：{labels[method as Method] ?? method}。
      {warning ? reason : "请结合原文判断相关性。"}
    </p>
  )
}
