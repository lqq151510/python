import { client } from "@/client/client.gen"
import type { KnowledgeSource, RetrievalMethod } from "@/lib/knowledge"

export interface ChatHistorySummary {
  id: string
  message: string
  answer_preview: string
  source_count: number
  retrieval_method?: RetrievalMethod | null
  created_at: string
}

export interface ChatHistoryDetail {
  id: string
  message: string
  answer: string
  request_id: string
  sources: KnowledgeSource[]
  retrieval_method?: RetrievalMethod | null
  retrieval_warning?: string | null
  created_at: string
}

export interface ChatHistoryResponse {
  data: ChatHistorySummary[]
  count: number
}

const security = [{ scheme: "bearer", type: "http" }] as const

export async function listChatHistory(): Promise<ChatHistoryResponse> {
  const response = await client.get<
    { 200: ChatHistoryResponse },
    unknown,
    true
  >({
    url: "/api/v1/chat/history",
    security: [...security],
    throwOnError: true,
  })
  return response.data
}

export async function readChatHistory(
  turnId: string,
): Promise<ChatHistoryDetail> {
  const response = await client.get<{ 200: ChatHistoryDetail }, unknown, true>({
    url: `/api/v1/chat/history/${encodeURIComponent(turnId)}`,
    security: [...security],
    throwOnError: true,
  })
  return response.data
}
