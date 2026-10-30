import type { CodeWorkspaceFile } from "@/lib/code"
import type { ImageRecord } from "@/lib/images"
import type { DocumentRecord, KnowledgeSource } from "@/lib/knowledge"
import type { NoteRecord } from "@/lib/notes"

export type WsKind = "doc" | "note" | "image" | "code"
export type WsScope = "all" | WsKind

export interface WsItem {
  id: string
  kind: WsKind
  refId: string
  title: string
  source: string
  summary: string
  meta: string[]
  updated: string
  /** 预拼接的小写检索文本，避免每次输入都重新拼字符串。 */
  haystack: string
  path?: string
  sizeBytes?: number
  documentId?: string
}

export interface WsDataset {
  items: WsItem[]
  counts: Record<WsScope, number>
  documents: DocumentRecord[]
  notes: NoteRecord[]
  images: ImageRecord[]
  codeFiles: CodeWorkspaceFile[]
  totalCodeBytes: number
  documentTotal: number
  noteTotal: number
  failedSources: WsKind[]
}

export const KIND_LABEL: Record<WsKind, string> = {
  doc: "文档",
  note: "笔记",
  image: "图片",
  code: "代码",
}

export const KIND_SOURCE: Record<WsKind, string> = {
  doc: "知识库",
  note: "我的笔记",
  image: "图片库",
  code: "代码仓库",
}

export function docItem(record: DocumentRecord): WsItem {
  return {
    id: `doc:${record.id}`,
    kind: "doc",
    refId: record.id,
    title: record.filename,
    source: KIND_SOURCE.doc,
    summary: `${record.chunk_count} 个片段 · ${formatBytes(record.size_bytes)}`,
    meta: [`${record.chunk_count} 段`, formatBytes(record.size_bytes)],
    updated: record.created_at,
    haystack: `${record.filename} 文档 知识库`.toLowerCase(),
    sizeBytes: record.size_bytes,
  }
}

export function noteItem(record: NoteRecord): WsItem {
  const body = record.body.replace(/\s+/g, " ").trim()
  return {
    id: `note:${record.id}`,
    kind: "note",
    refId: record.id,
    title: record.title,
    source: KIND_SOURCE.note,
    summary: body.length > 140 ? `${body.slice(0, 140)}…` : body,
    meta: [],
    updated: record.updated_at || record.created_at,
    haystack: `${record.title} ${record.body} 笔记`.toLowerCase(),
  }
}

export function imageItem(record: ImageRecord): WsItem {
  return {
    id: `image:${record.id}`,
    kind: "image",
    refId: record.id,
    title: record.filename,
    source: KIND_SOURCE.image,
    summary: `${formatBytes(record.size_bytes)} · ${record.media_type}`,
    meta: [formatBytes(record.size_bytes)],
    updated: record.created_at,
    haystack: `${record.filename} 图片`.toLowerCase(),
    sizeBytes: record.size_bytes,
  }
}

export function codeItem(file: CodeWorkspaceFile): WsItem {
  return {
    id: `code:${file.path}`,
    kind: "code",
    refId: file.path,
    title: file.path.split("/").pop() ?? file.path,
    source: KIND_SOURCE.code,
    summary: file.path,
    meta: [formatBytes(file.size_bytes)],
    updated: "",
    haystack: `${file.path} 代码`.toLowerCase(),
    path: file.path,
    sizeBytes: file.size_bytes,
  }
}

export function buildDataset(input: {
  documents: DocumentRecord[]
  notes: NoteRecord[]
  images: ImageRecord[]
  code: { files: CodeWorkspaceFile[]; total_bytes: number }
}): WsDataset {
  const codeItems = input.code.files.map(codeItem)
  const items = [
    ...input.documents.map(docItem),
    ...input.notes.map(noteItem),
    ...input.images.map(imageItem),
    ...codeItems,
  ]
  const counts: Record<WsScope, number> = {
    all: items.length,
    doc: input.documents.length,
    note: input.notes.length,
    image: input.images.length,
    code: codeItems.length,
  }
  return {
    items,
    counts,
    documents: input.documents,
    notes: input.notes,
    images: input.images,
    codeFiles: input.code.files,
    totalCodeBytes: input.code.total_bytes,
    documentTotal: input.documents.length,
    noteTotal: input.notes.length,
    failedSources: [],
  }
}

export interface RankedItem {
  item: WsItem
  score: number
  snippet: string
}

const CJK = /[\u4e00-\u9fff]/

export function tokensOf(query: string): string[] {
  const raw = query.trim().toLowerCase()
  if (!raw) return []
  const tokens = new Set<string>()
  for (const part of raw.split(/\s+/)) {
    if (!part) continue
    tokens.add(part)
    if (CJK.test(part) && part.length > 1) {
      for (let index = 0; index < part.length - 1; index += 1) {
        tokens.add(part.slice(index, index + 2))
      }
    }
  }
  return [...tokens].slice(0, 24)
}

export function rankItems(
  items: WsItem[],
  scope: WsScope,
  query: string,
): RankedItem[] {
  const pool = scope === "all" ? items : items.filter((i) => i.kind === scope)
  const tokens = tokensOf(query)
  if (!tokens.length) {
    return pool
      .slice()
      .sort((left, right) =>
        (right.updated || "").localeCompare(left.updated || ""),
      )
      .map((item) => ({ item, score: 0, snippet: item.summary }))
  }
  const ranked: RankedItem[] = []
  for (const item of pool) {
    const title = item.title.toLowerCase()
    const summary = item.summary.toLowerCase()
    let score = 0
    for (const token of tokens) {
      if (title.includes(token)) score += 8
      if (summary.includes(token)) score += 3
      if (item.haystack.includes(token)) score += 1
    }
    if (!score) continue
    ranked.push({ item, score, snippet: snippetAround(item.summary, tokens) })
  }
  return ranked.sort(
    (left, right) =>
      right.score - left.score ||
      (right.item.updated || "").localeCompare(left.item.updated || ""),
  )
}

function snippetAround(text: string, tokens: string[]): string {
  const lowered = text.toLowerCase()
  const hit = tokens
    .map((token) => lowered.indexOf(token))
    .filter((index) => index >= 0)
    .sort((left, right) => left - right)[0]
  if (hit === undefined || text.length <= 150) return text
  const start = Math.max(0, hit - 40)
  const end = Math.min(text.length, start + 150)
  return `${start > 0 ? "…" : ""}${text.slice(start, end)}${
    end < text.length ? "…" : ""
  }`
}

export function sourcesToItems(sources: KnowledgeSource[]): WsItem[] {
  return sources.map<WsItem>((source) => ({
    id: `doc:${source.document_id}`,
    kind: "doc",
    refId: source.document_id,
    title: source.filename,
    source: KIND_SOURCE.doc,
    summary: `[${source.citation}] ${source.heading || "正文"} · ${source.content
      .replace(/\s+/g, " ")
      .slice(0, 120)}`,
    meta: [`第 ${source.line_start}-${source.line_end} 行`],
    updated: "",
    haystack:
      `${source.filename} ${source.heading} ${source.content}`.toLowerCase(),
    documentId: source.document_id,
  }))
}

export function sourcesToRanked(sources: KnowledgeSource[]): RankedItem[] {
  const grouped = new Map<
    string,
    { item: WsItem; score: number; snippet: string; hits: number }
  >()
  for (const source of sources) {
    const item = sourcesToItems([source])[0]
    const snippet = source.content.replace(/\s+/g, " ").slice(0, 160)
    const existing = grouped.get(item.id)
    if (existing) {
      existing.hits += 1
      existing.score = Math.max(existing.score, source.score)
      continue
    }
    grouped.set(item.id, { item, score: source.score, snippet, hits: 1 })
  }
  return [...grouped.values()].map(({ item, score, snippet, hits }) => ({
    item:
      hits > 1
        ? {
            ...item,
            meta: [...item.meta, `${hits} 处命中`],
            summary: `${item.summary}（${hits} 处命中）`,
          }
        : item,
    score,
    snippet,
  }))
}

const SYMBOL_PATTERNS = [
  /(?:^|\n)\s*(?:export\s+)?(?:async\s+)?def\s+([A-Za-z_]\w*)/g,
  /(?:^|\n)\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_]\w*)/g,
  /(?:^|\n)\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)/g,
  /(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$]\w*)\s*=/g,
]

/** 从代码里挑出可当作检索词的符号名，供「查找相关笔记」使用。 */
export function extractSymbols(content: string, limit = 3): string[] {
  const found: string[] = []
  for (const pattern of SYMBOL_PATTERNS) {
    pattern.lastIndex = 0
    let match = pattern.exec(content)
    while (match) {
      const name = match[1]
      if (name && name.length >= 3 && !found.includes(name)) found.push(name)
      if (found.length >= limit) return found
      match = pattern.exec(content)
    }
  }
  return found
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  const month = `${date.getMonth() + 1}`.padStart(2, "0")
  const day = `${date.getDate()}`.padStart(2, "0")
  return `${date.getFullYear()}-${month}-${day}`
}

export interface CtxEntry {
  key: string
  title: string
  source: string
  excerpt: string
  documentId?: string
  path?: string
}

export interface AskTurn {
  id: string
  question: string
  answer?: string
  sources?: KnowledgeSource[]
  error?: string
  errorCode?: string
  pending?: boolean
}
