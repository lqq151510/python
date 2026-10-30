import { isAxiosError } from "axios"

import { client } from "@/client/client.gen"

export interface DocumentRecord {
  id: string
  filename: string
  size_bytes: number
  chunk_count: number
  created_at: string
}

export interface KnowledgeSource {
  citation: string
  document_id: string
  chunk_id: string
  filename: string
  heading: string
  line_start: number
  line_end: number
  content: string
  score: number
}

export type RetrievalMethod = "bm25" | "vector" | "hybrid"

export interface SearchResult {
  warning?: string | null
  query: string
  method: string
  sources: KnowledgeSource[]
  document_id?: string | null
}

export interface DocumentChunk {
  id: string
  position: number
  heading: string
  line_start: number
  line_end: number
  content: string
}

export interface DocumentDetail {
  document: DocumentRecord
  chunks: DocumentChunk[]
}

export const validDocumentId = (value: unknown): string | undefined =>
  typeof value === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
    ? value
    : undefined

export async function readDocument(
  documentId: string,
): Promise<DocumentDetail> {
  const response = await client.get<{ 200: DocumentDetail }, unknown, true>({
    url: `/api/v1/documents/${encodeURIComponent(documentId)}`,
    security: [...security],
    timeout: 15_000,
    throwOnError: true,
  })
  return response.data
}

const security = [{ scheme: "bearer", type: "http" }] as const

export async function listDocuments() {
  const response = await client.get<
    { 200: { data: DocumentRecord[]; count: number } },
    unknown,
    true
  >({ url: "/api/v1/documents", security: [...security], throwOnError: true })
  return response.data
}

export async function uploadDocument(file: File) {
  const body = new FormData()
  body.append("file", file)
  const response = await client.post<
    { 201: { document: DocumentRecord; duplicate: boolean } },
    unknown,
    true
  >({
    url: "/api/v1/documents",
    security: [...security],
    body,
    timeout: 15_000,
    throwOnError: true,
  })
  return response.data
}

export async function searchDocuments(
  query: string,
  documentId?: string,
  method: RetrievalMethod = "bm25",
  signal?: AbortSignal,
): Promise<SearchResult> {
  const response = await client.post<{ 200: SearchResult }, unknown, true>({
    url: "/api/v1/documents/search",
    security: [...security],
    body: {
      query,
      ...(method === "bm25" ? {} : { method }),
      limit: 5,
      ...(documentId ? { document_id: documentId } : {}),
    },
    headers: { "Content-Type": "application/json" },
    signal,
    timeout: 65_000,
    throwOnError: true,
  })
  return response.data
}

export async function deleteDocument(id: string) {
  await client.delete({
    url: `/api/v1/documents/${encodeURIComponent(id)}`,
    security: [...security],
    throwOnError: true,
  })
}

/** 用户取消或被新输入作废的请求，不能展示成检索失败。 */
export function isRequestCancelled(error: unknown): boolean {
  if (!error || typeof error !== "object") return false
  if (isAxiosError(error)) return error.code === "ERR_CANCELED"
  if (!(error instanceof Error)) return false
  return (
    error.name === "AbortError" ||
    error.name === "CanceledError" ||
    error.name === "CancelledError" ||
    error.message === "CancelledError"
  )
}

export function describeKnowledgeError(error: unknown): string {
  if (isAxiosError(error)) {
    if (!error.response) return "无法连接服务，请确认后端已启动。"
    const detail = error.response.data?.detail
    if (typeof detail === "string") return detail
    if (detail && typeof detail.message === "string") return detail.message
    if (error.response.status === 422)
      return "输入不符合要求，请检查文件或检索内容。"
  }
  return "操作失败，请稍后重试。"
}
