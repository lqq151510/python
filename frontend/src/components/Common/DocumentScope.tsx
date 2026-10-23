import type { DocumentRecord } from "@/lib/knowledge"

export function DocumentScope({
  value,
  documents,
  disabled = false,
  onChange,
}: {
  value: string
  documents: DocumentRecord[]
  disabled?: boolean
  onChange: (value: string) => void
}) {
  return (
    <label className="da-document-scope">
      <span>检索范围</span>
      <select
        aria-label="检索范围"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">全部文档</option>
        {value && !documents.some((document) => document.id === value) && (
          <option value={value}>指定文档（列表中不可用）</option>
        )}
        {documents.map((document) => (
          <option key={document.id} value={document.id}>
            {document.filename}
          </option>
        ))}
      </select>
    </label>
  )
}
