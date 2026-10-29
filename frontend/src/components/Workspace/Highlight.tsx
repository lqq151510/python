import type { ReactNode } from "react"

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/** 把命中的检索词包成 <mark>，避免逐字符高亮导致 DOM 过碎。 */
export function Highlight({
  text,
  tokens,
  className = "zm-mark",
}: {
  text: string
  tokens: string[]
  className?: string
}): ReactNode {
  const usable = tokens.filter((token) => token.length > 0).slice(0, 12)
  if (!usable.length) return text
  const pattern = new RegExp(`(${usable.map(escapeRegExp).join("|")})`, "gi")
  const parts = text.split(pattern)
  return parts.map((part, index) =>
    pattern.test(part) ? (
      // 同一段文本里的重复命中没有稳定 key，用位置兜底。
      <mark className={className} key={`${part}-${index}`}>
        {part}
      </mark>
    ) : (
      part
    ),
  )
}
